"""CM-03 conversation engine: one deterministic state machine per WhatsApp conversation.

    NEW ──registered sender──► AWAIT_OTP ──code ok──► VERIFIED ⇄ AWAIT_PTP_DATE / AWAIT_UTR
     │                            │  3 wrong ─► HANDOFF (OTP_LOCKED)        │
     └─unregistered─► AWAIT_LOAN_ID ─► HANDOFF (UNREGISTERED_NUMBER)        ├─► resolved outcome(s) ─► CLOSED
                                                                            └─► HANDOFF (hardship, dispute, fraud…)
Order inside one inbound message:
  1. log INBOUND (OTP-looking digits and phone numbers masked)
  2. a conversation already handed to a human only gets a holding reply (STOP still honoured)
  3. fraud text detectors (injection, phishing link, spoofed lender, OTP social engineering,
     other people's data) -> refuse + FRAUD_SUSPECT + ticket + hand off
  4. STOP -> Ledger.record_stop (all channels) when the sender is the registered number
  5. not verified -> identify + OTP only. Nothing about the account is looked up for the reply,
     and only PRE_VERIFY_SAFE copy may be sent (enforced in _reply). A request made before
     verification is parked and served right after the OTP succeeds.
  6. verified -> intent -> tool calls -> reply; collection intents go through K13 (exceptions.py)

"Resolved" = at least one request fulfilled or a correct terminal outcome reached (balance shared,
link sent, PTP recorded, claim recorded / verified, document sent, STOP honoured, wrong number
closed) AND no hand-off. Hardship, dispute, callback, fraud, OTP lockout, unknown-after-clarify are
hand-offs: correct behaviour, but they count against automation.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

from spine.ledger import PaymentError

from .. import intent as k09
from ..policy import Policy
from . import PROVENANCE
from . import k10 as K10
from . import nlu as N
from .copy import COPY, LENDER, PRE_VERIFY_SAFE, render
from .fraud import (ALREADY_APPLIED, FAKE, PENDING, REVERSED, SETTLED, Signal, check_attachment, impersonation,
                    scan_text)
from .otp import EXPIRED, LOCKED, NO_OTP, VERIFIED, WRONG
from .store import dumps, init_schema, mask_phone, scrub
from .tools import DryRunWhatsAppAdapter, ToolBox, Turn

NEW, AWAIT_LOAN_ID, AWAIT_OTP = "NEW", "AWAIT_LOAN_ID", "AWAIT_OTP"
AWAIT_NAME, UNREGISTERED = "AWAIT_NAME", "UNREGISTERED"   # identity_mode="k10" only
S_VERIFIED, AWAIT_PTP_DATE, AWAIT_UTR = "VERIFIED", "AWAIT_PTP_DATE", "AWAIT_UTR"
HANDOFF, CLOSED = "HANDOFF", "CLOSED"

RESOLVED_OUTCOMES = {"BALANCE_SHARED", "NO_DUES", "LINK_SENT", "CLAIM_VERIFIED_RECEIPT", "CLAIM_PENDING_RECHECK",
                     "CLAIM_RECORDED_NO_REF", "RECEIPT_RESENT", "PTP_RECORDED", "PTP_RECORDED_NO_HOLD", "NEEDS_TIME_NOTED",
                     "DOC_SENT", "NOC_SENT", "NOC_INFO", "LOAN_DETAILS_SHARED", "STOP_HONOURED", "WRONG_NUMBER_CLOSED"}
SESSION_TTL = timedelta(minutes=30)
IDENTITY_MODES = ("otp", "k10")
# identity_mode="k10": our reply keys that K10 scripts.csv also has, rendered from his scripts instead.
K10_SCRIPT_FOR = {"HARDSHIP": "HARDSHIP_ACK", "CALLBACK": "CALLBACK_CREATED", "STOP_ACK": "STOP_CONFIRMED",
                  "WRONG_NUMBER": "WRONG_PERSON", "ROUTED": "ROUTED", "ASK_PTP_DATE": "NT_ASK_DATE",
                  "PTP_CONFIRM": "PTP_CAPTURED", "ASK_UTR": "PAY_ASK_PROOF", "UNREGISTERED": "UNREGISTERED",
                  "BROKEN_PTP_PUSH": "BROKEN_PTP_PUSH", "END_CONVERSATION": "END_CONVERSATION"}


def normalize_sender(sender: str) -> str:
    s = re.sub(r"[\s\-().]", "", str(sender or ""))
    if s and not s.startswith("+"):
        s = "+" + s.lstrip("0") if not s.startswith("00") else "+" + s[2:]
    return s


def _rupees(paise: int) -> str:
    r = paise / 100
    return f"{r:,.0f}" if r == int(r) else f"{r:,.2f}"


def _fmt_date(d: str) -> str:
    return date.fromisoformat(d).strftime("%d %b %Y")


def _last4(loan_id: str) -> str:
    return re.sub(r"\D", "", loan_id)[-4:].rjust(4, "0")


@dataclass
class Conversation:
    conversation_id: str
    sender: str
    state: str = NEW
    borrower_id: Optional[str] = None
    lang: str = "en"
    verified: bool = False
    verified_at: Optional[str] = None
    data: dict = field(default_factory=dict)
    outcomes: List[str] = field(default_factory=list)
    handoff_reason: Optional[str] = None
    fraud: bool = False
    turns: int = 0
    first_reply_ms: Optional[float] = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def resolved(self) -> bool:
        return self.handoff_reason is None and any(o in RESOLVED_OUTCOMES for o in self.outcomes)

    def public(self) -> dict:
        return {"conversation_id": self.conversation_id, "sender": mask_phone(self.sender), "state": self.state,
                "borrower_id": self.borrower_id if self.verified else None, "lang": self.lang,
                "verified": self.verified, "outcomes": list(self.outcomes), "resolved": self.resolved,
                "handoff_reason": self.handoff_reason, "fraud_suspect": self.fraud, "turns": self.turns,
                "first_reply_ms": self.first_reply_ms, "provenance": PROVENANCE}


class ConversationEngine:
    def __init__(self, ledger, clock: Optional[Callable[[], datetime]] = None,
                 adapter: Optional[DryRunWhatsAppAdapter] = None, classifier: Optional[k09.IntentClassifier] = None,
                 otp_code_source: Optional[Callable[[], str]] = None, ptp_max_days: Optional[int] = None,
                 identity_mode: Optional[str] = None, phraser=None):
        self.ledger = ledger
        # sarvam-105b-conversations phrasing (kairos_backend/sarvam/phrasing.py): only after this engine chose
        # the state, the action and the reply key; validator-checked; the approved line verbatim on any failure.
        # None = off (the default for tests / evals); "auto" = on when a Sarvam key is configured.
        if phraser == "auto":
            from ..sarvam.phrasing import shared_phraser
            phraser = shared_phraser(lender=LENDER)
        self.phraser = phraser
        # "otp" (default): registered number + OTP (CM-03 brief). "k10": Ojas's K10 - registered number + name
        # confirmation, OTP step-up only if K10 otp_step_up.enabled. Which is the default: OPEN_DECISION K10-1.
        self.identity_mode = identity_mode or "otp"
        if self.identity_mode not in IDENTITY_MODES:
            raise ValueError(f"identity_mode must be one of {IDENTITY_MODES}")
        self.policy: Policy = ledger.policy
        self.clock = clock or self.policy.now
        init_schema(ledger._conn())
        self.tools = ToolBox(ledger, self.clock, adapter, otp_code_source)
        if classifier is None:
            llm = k09.LunaClient() if k09.LunaClient().api_key() else None   # LLM only when a key is set
            classifier = k09.IntentClassifier(llm=llm)
        self.nlu = N.Nlu(classifier)
        self.ptp_max_days = ptp_max_days or (self.policy.exceptions.get("promise_to_pay") or {}).get("auto_accept_max_days", 7)
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @property
    def adapter(self) -> DryRunWhatsAppAdapter:
        return self.tools.adapter

    # ------------------------------------------------------------------ persistence
    def _lock_for(self, sender: str) -> threading.Lock:
        with self._locks_guard:
            lk = self._locks.get(sender)
            if lk is None:
                lk = self._locks[sender] = threading.Lock()
            return lk

    def _row_to_conv(self, r) -> Conversation:
        return Conversation(r["conversation_id"], r["sender"], r["state"], r["borrower_id"], r["lang"], bool(r["verified"]),
                            r["verified_at"], json.loads(r["data"]), json.loads(r["outcomes"]), r["handoff_reason"],
                            bool(r["fraud"]), r["turns"], r["first_reply_ms"], r["created_at"], r["updated_at"])

    def get(self, conversation_id: str) -> Optional[Conversation]:
        r = self.ledger._conn().execute("SELECT * FROM cm03_conversations WHERE conversation_id=?",
                                        (conversation_id,)).fetchone()
        return self._row_to_conv(r) if r else None

    def _load_or_create(self, sender: str) -> Conversation:
        now = self.clock()
        r = self.ledger._conn().execute(
            "SELECT * FROM cm03_conversations WHERE sender=? AND state<>? ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (sender, CLOSED)).fetchone()
        if r and datetime.fromisoformat(r["updated_at"]) >= now - SESSION_TTL:
            return self._row_to_conv(r)
        iso = now.isoformat(timespec="seconds")
        return Conversation("WA-" + uuid.uuid4().hex[:12], sender, created_at=iso, updated_at=iso)

    def _save(self, conv: Conversation, turn: Turn, message_id: Optional[str]) -> None:
        conv.updated_at = self.clock().isoformat(timespec="seconds")
        with self.ledger.tx() as c:
            c.execute(
                "INSERT INTO cm03_conversations (conversation_id, sender, sender_masked, borrower_id, state, lang, verified,"
                " verified_at, data, outcomes, handoff_reason, fraud, turns, first_reply_ms, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET"
                " borrower_id=excluded.borrower_id, state=excluded.state, lang=excluded.lang, verified=excluded.verified,"
                " verified_at=excluded.verified_at, data=excluded.data, outcomes=excluded.outcomes,"
                " handoff_reason=excluded.handoff_reason, fraud=excluded.fraud, turns=excluded.turns,"
                " first_reply_ms=excluded.first_reply_ms, updated_at=excluded.updated_at",
                (conv.conversation_id, conv.sender, mask_phone(conv.sender), conv.borrower_id, conv.state, conv.lang,
                 int(conv.verified), conv.verified_at, dumps(conv.data), dumps(conv.outcomes), conv.handoff_reason,
                 int(conv.fraud), conv.turns, conv.first_reply_ms, conv.created_at, conv.updated_at))
            c.executemany("INSERT INTO cm03_timeline (conversation_id, ts, kind, name, provenance, verified, payload)"
                          " VALUES (?,?,?,?,?,?,?)",
                          [(conv.conversation_id, e["ts"], e["kind"], e["name"], e["provenance"], e["verified"],
                            dumps(e["payload"])) for e in turn.envelopes])
            if message_id:
                c.execute("INSERT OR IGNORE INTO cm03_inbound_ids (message_id, conversation_id, replies) VALUES (?,?,?)",
                          (message_id, conv.conversation_id, dumps([r["text"] for r in turn.replies])))

    def timeline(self, conversation_id: str) -> List[dict]:
        rows = self.ledger._conn().execute("SELECT * FROM cm03_timeline WHERE conversation_id=? ORDER BY id",
                                           (conversation_id,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "name": r["name"], "provenance": r["provenance"],
                 "verified": bool(r["verified"]), "payload": json.loads(r["payload"])} for r in rows]

    # ------------------------------------------------------------------ entry point
    def handle(self, sender: str, text: Optional[str] = None, attachment: Optional[dict] = None,
               button: Optional[str] = None, message_id: Optional[str] = None) -> dict:
        t0 = time.perf_counter()
        sender = normalize_sender(sender)
        with self._lock_for(sender):
            if message_id:
                dup = self.ledger._conn().execute("SELECT * FROM cm03_inbound_ids WHERE message_id=?", (message_id,)).fetchone()
                if dup:  # webhook retry: same answer, no second side effect
                    conv = self.get(dup["conversation_id"])
                    return {**(conv.public() if conv else {}), "replies": json.loads(dup["replies"]), "duplicate": True,
                            "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
            conv = self._load_or_create(sender)
            conv.turns += 1
            turn = Turn(conv.conversation_id, conv.verified)
            body = (button or text or "").strip()
            turn.add("INBOUND", "whatsapp", {"text": scrub(body), "attachment": _att_summary(attachment),
                                             "message_id": message_id}, self._iso())
            try:
                self._step(conv, turn, body, attachment)
            except Exception as e:  # never leave the borrower without an answer; a human picks it up
                turn.add("ERROR", type(e).__name__, {"error": str(e)[:200]}, self._iso())
                self._handoff(conv, turn, "ENGINE_ERROR", "UNKNOWN_HANDOFF", ticket=False)
            ms = round((time.perf_counter() - t0) * 1000, 2)
            if conv.first_reply_ms is None and turn.replies:
                conv.first_reply_ms = ms
            self._save(conv, turn, message_id)
        return {**conv.public(), "replies": [r["text"] for r in turn.replies], "reply_keys": [r["key"] for r in turn.replies],
                "tool_calls": [e["name"] for e in turn.envelopes if e["kind"] == "TOOL_CALL"], "latency_ms": ms}

    def _iso(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    # ------------------------------------------------------------------ replies, hand-offs, fraud
    def _reply(self, conv: Conversation, turn: Turn, key: str, **kw) -> None:
        if not conv.verified and key not in PRE_VERIFY_SAFE:
            # Disclosure guard: the engine never sends account copy before OTP verification.
            turn.add("GUARD_BLOCK", key, {"reason": "account copy requested before verification"}, self._iso())
            key, kw = "PRE_VERIFY_NEED", {}
        if self.identity_mode == "k10" and (key in K10_SCRIPT_FOR or (key in K10.scripts() and key not in COPY)):
            key = K10_SCRIPT_FOR.get(key, key)
            text = K10.render(key, self._k10_lang(conv), **self._k10_fields(conv, kw))
        else:
            text = render(key, conv.lang, **kw)
        phrasing = None
        if self.phraser is not None and self.phraser.eligible(key, conv.verified):
            pr = self.phraser.phrase(key, text, conv.lang, bool(conv.data.get("hinglish")), conv.verified)
            text, phrasing = pr.text, pr.envelope()
        self.tools.send_whatsapp_reply(turn, conv.sender, text)
        turn.replies.append({"key": key, "text": text})
        payload = {"text": scrub(text), "to": mask_phone(conv.sender), "lang": conv.lang}
        if phrasing is not None:
            payload["phrasing"] = phrasing
        turn.add("REPLY", key, payload, self._iso())

    def _k10_lang(self, conv: Conversation) -> str:
        if conv.lang == "hi":
            return "hi"
        return "hinglish" if conv.data.get("hinglish") else "en"

    def _k10_fields(self, conv: Conversation, kw: dict) -> dict:
        f = {"lender_name": LENDER, "first_name": conv.data.get("first_name", ""), "link": kw.get("link", ""),
             "ptp_date": kw.get("date", "")}
        f.update(K10.date_options(self.clock().date()))
        return f

    def _outcome(self, conv: Conversation, turn: Turn, outcome: str) -> None:
        conv.outcomes.append(outcome)
        turn.add("OUTCOME", outcome, {"resolved_by_bot": outcome in RESOLVED_OUTCOMES}, self._iso())

    def _handoff(self, conv: Conversation, turn: Turn, reason: str, key: Optional[str], ticket: bool = True,
                 summary: Optional[tuple] = None, evidence: tuple = (), **kw) -> None:
        if ticket:
            self.tools.create_ticket(turn, reason, conv.borrower_id, conv.data.get("obligation_id"),
                                     summary or (f"CM-03 WhatsApp hand-off: {reason}", f"conversation {conv.conversation_id}"),
                                     evidence)
        conv.state = HANDOFF
        conv.handoff_reason = conv.handoff_reason or reason
        turn.add("HANDOFF", reason, {"queue": "COLLECTIONS_SUPERVISOR"}, self._iso())
        if key:
            self._reply(conv, turn, key, **kw)

    def _fraud(self, conv: Conversation, turn: Turn, signals: List[Signal], key: str = "FRAUD_REFUSE") -> None:
        conv.fraud = True
        kinds = sorted({s.kind for s in signals})
        turn.add("FRAUD_SUSPECT", ",".join(kinds), {"signals": [s.as_dict() for s in signals], "disclosed": False},
                 self._iso())
        self.tools.create_ticket(turn, "FRAUD_SUSPECT", conv.borrower_id, None,
                                 (f"CM-03 fraud suspect: {', '.join(kinds)}"[:160], signals[0].reason[:160]),
                                 (conv.conversation_id,), audit_type="FRAUD_SUSPECT")
        self._handoff(conv, turn, "FRAUD_SUSPECT", key, ticket=False)

    # ------------------------------------------------------------------ the state machine
    def _step(self, conv: Conversation, turn: Turn, text: str, attachment: Optional[dict]) -> None:
        req = N.language_request(text)
        det = N.detect_lang(text)
        if req:
            conv.lang = req
        elif det == "hi" and (conv.turns == 1 or N._DEVANAGARI.search(text)):
            conv.lang = "hi"
        elif det == "en" and conv.turns == 1:
            conv.lang = "en"
        if conv.lang != "hi" and text:
            conv.data["hinglish"] = bool(N._HINGLISH.search(text)) and not N._DEVANAGARI.search(text)

        if conv.state == HANDOFF:
            if k09.is_stop(text):
                self._stop(conv, turn)
                conv.state = HANDOFF
                return
            self._reply(conv, turn, "HANDOFF_HOLD")
            return

        signals = scan_text(text)
        if attachment and not conv.verified:
            # image text is read for embedded instructions only; the payment itself is checked after OTP
            signals += [s for s in check_attachment({"ocr": {"text": (attachment.get("ocr") or {}).get("text")}},
                                                    self.clock(), None)]
        if signals and self.identity_mode == "k10" and all(s.kind == "PROMPT_INJECTION" for s in signals):
            # K10 untrusted_input: ignore the instruction, log INJECTION_ATTEMPT, reply INJECTION_SAFE, carry on.
            conv.data["injection_attempts"] = conv.data.get("injection_attempts", 0) + 1
            turn.add("INJECTION_ATTEMPT", "PROMPT_INJECTION", {"signals": [s.as_dict() for s in signals],
                                                               "disclosed": False}, self._iso())
            self._reply(conv, turn, "INJECTION_SAFE")
            return
        if signals:
            self._fraud(conv, turn, signals)
            return
        if k09.is_stop(text) or k09.is_stop(N.fix_typos(text)):
            self._stop(conv, turn)
            return
        if not conv.verified:
            self._pre_verify(conv, turn, text, attachment)
            return
        self._serve(conv, turn, text, attachment)

    # ---------------- before verification: identify + OTP, nothing else ----------------
    def _pre_verify(self, conv: Conversation, turn: Turn, text: str, attachment: Optional[dict]) -> None:
        code = N.extract_otp(text) if conv.state == AWAIT_OTP else None
        if code:
            res = self.tools.verify_otp(turn, conv.conversation_id, conv.borrower_id, conv.sender, code)
            if res.status == VERIFIED:
                conv.verified, turn.verified = True, True
                conv.verified_at = self._iso()
                conv.state = S_VERIFIED
                turn.add("STATE", "VERIFIED", {"binds": "sender+conversation+account"}, self._iso())
                pending = conv.data.pop("pending", None)
                if pending:
                    self._reply(conv, turn, "VERIFIED_OK")
                    self._serve(conv, turn, pending.get("text") or "", pending.get("attachment"))
                else:
                    self._reply(conv, turn, "VERIFIED_MENU")
            elif res.status == WRONG:
                self._reply(conv, turn, "OTP_WRONG", left=res.attempts_left)
            elif res.status == LOCKED:
                self._handoff(conv, turn, "OTP_LOCKED", "OTP_LOCKED")
            else:  # EXPIRED / NO_OTP
                self._reply(conv, turn, "OTP_EXPIRED")
            return

        u = self.nlu.understand(text, self.clock(), verified=False) if text else N.Understanding(k09.OTHER, None)
        if self._before_verification(conv, turn, text, u):
            return
        if self.identity_mode == "k10" and conv.state in (NEW, AWAIT_NAME, UNREGISTERED):
            self._k10_identity(conv, turn, text, attachment, u)
            return
        if u.intent == k09.WRONG_PERSON:
            turn.add("STATE", "WRONG_NUMBER", {"review": "number flagged for contact-data review"}, self._iso())
            self._outcome(conv, turn, "WRONG_NUMBER_CLOSED")
            self._reply(conv, turn, "WRONG_NUMBER")
            conv.state = CLOSED
            return

        if conv.state == AWAIT_OTP:
            if u.intent == N.RESEND_OTP:
                acct = self.tools.lookup_account(turn, phone=conv.sender)
                if acct and self.tools.send_otp(turn, conv.conversation_id, conv.borrower_id, conv.sender, acct["phone"], conv.lang):
                    self._reply(conv, turn, "OTP_RESENT")
                elif self.tools.otp.locked_until(conv.borrower_id):
                    self._handoff(conv, turn, "OTP_LOCKED", "OTP_LOCKED")
                else:
                    self._handoff(conv, turn, "OTP_SEND_LIMIT", "OTP_LIMIT")
                return
            if u.intent == N.LANG_CHANGE:
                self._reply(conv, turn, "LANG_SET")
            self._park(conv, text, attachment, u)
            self._reply(conv, turn, "PRE_VERIFY_NEED" if u.intent not in (N.GREETING, N.LANG_CHANGE) else "OTP_PROMPT")
            return

        if conv.state == NEW:
            acct = self.tools.lookup_account(turn, phone=conv.sender)
            if acct:
                conv.borrower_id = acct["borrower_id"]
                if self.tools.otp.locked_until(conv.borrower_id):
                    self._handoff(conv, turn, "OTP_LOCKED", "OTP_LOCKED")
                    return
                self._park(conv, text, attachment, u)
                sent = self.tools.send_otp(turn, conv.conversation_id, conv.borrower_id, conv.sender, acct["phone"], conv.lang)
                conv.state = AWAIT_OTP
                if u.intent == N.LANG_CHANGE:
                    self._reply(conv, turn, "LANG_SET")
                self._reply(conv, turn, "GREET_VERIFY") if sent else self._handoff(conv, turn, "OTP_SEND_LIMIT", "OTP_LIMIT")
                return
            conv.data["unregistered_ask"] = text[:300]
            ids = N.extract_loan_ids(text)
            if not ids:
                conv.state = AWAIT_LOAN_ID
                self._reply(conv, turn, "ASK_LOAN_ID")
                return
        # unregistered sender (NEW with a loan id, or AWAIT_LOAN_ID)
        ids = N.extract_loan_ids(text)
        asked = (conv.data.get("unregistered_ask") or "") + " " + text
        sig = impersonation(False, asked, ids)
        if sig:
            self._fraud(conv, turn, [sig])
            return
        if ids:
            # Same answer whether or not the loan exists: no account enumeration before verification.
            self.tools.lookup_account(turn, loan_id=ids[0])
            self._handoff(conv, turn, "UNREGISTERED_NUMBER", "UNREGISTERED")
            return
        conv.data["loan_id_asks"] = conv.data.get("loan_id_asks", 0) + 1
        if conv.data["loan_id_asks"] >= 2:
            self._handoff(conv, turn, "UNIDENTIFIED", "UNKNOWN_HANDOFF")
        else:
            self._reply(conv, turn, "ASK_LOAN_ID")

    def _registered_borrower(self, conv: Conversation, turn: Turn) -> Optional[str]:
        if conv.borrower_id:
            return conv.borrower_id
        acct = self.tools.lookup_account(turn, phone=conv.sender)
        if acct:
            conv.borrower_id = acct["borrower_id"]
        return conv.borrower_id

    def _before_verification(self, conv: Conversation, turn: Turn, text: str, u: N.Understanding) -> bool:
        """K10 allowed_before_verification: abuse / legal threat / fraud claim end the chat and route; hardship
        is acknowledged and held; a callback is created. Only for the registered number, and nothing about the
        account goes into the reply."""
        safety = K10.safety_intent(text)
        if not safety and u.intent not in (k09.HARDSHIP, k09.CALLBACK):
            return False
        if self._registered_borrower(conv, turn) is None:
            return False
        if safety:
            self._end_conversation(conv, turn, safety)
        elif u.intent == k09.HARDSHIP:
            self._hardship(conv, turn)
        else:
            self._callback(conv, turn)
        return True

    def _end_conversation(self, conv: Conversation, turn: Turn, event: str) -> None:
        """K13 routed_exceptions ABUSIVE / LEGAL_THREAT / FRAUD_CLAIM: end the automated chat, no argument,
        supervisor ticket, outreach paused until a supervisor clears it."""
        got = self._oldest_due(conv, turn) if conv.borrower_id else None
        res = self.tools.k13_exception(turn, conv.borrower_id, event, self._band(conv, turn) if conv.borrower_id else "B1",
                                       got[1]["obligation_id"] if got else None,
                                       (f"WhatsApp: {event.lower().replace('_', ' ')}", "K13: conversation ended, no argument"))
        self._handoff(conv, turn, event, "END_CONVERSATION", ticket=not res.get("ticket_id"))

    def _k10_identity(self, conv: Conversation, turn: Turn, text: str, attachment: Optional[dict],
                      u: N.Understanding) -> None:
        """K10 section 4: registered number + name confirmation (2 attempts); OTP only as a step-up."""
        if u.intent == k09.WRONG_PERSON:
            self._outcome(conv, turn, "WRONG_NUMBER_CLOSED")
            self._reply(conv, turn, "WRONG_NUMBER")
            conv.state = CLOSED
            return
        if conv.state == UNREGISTERED:
            if re.search(r"^\W*call\W*$", text, re.I) or u.intent == k09.CALLBACK:
                self._handoff(conv, turn, "UNREGISTERED_CALLBACK", "CALLBACK")
            else:
                self._reply(conv, turn, "UNREGISTERED")
            return
        if conv.state == NEW:
            self._reply(conv, turn, "ACK")
            if self._registered_borrower(conv, turn) is None:
                conv.state = UNREGISTERED
                self._reply(conv, turn, "UNREGISTERED")
                return
            self._park(conv, text, attachment, u)
            conv.state = AWAIT_NAME
            self._reply(conv, turn, "ASK_NAME")
            return
        # AWAIT_NAME: compare with the name on record
        row = self.ledger._conn().execute("SELECT full_name FROM borrowers WHERE borrower_id=?",
                                          (conv.borrower_id,)).fetchone()
        on_record = row["full_name"] if row else ""
        tries = conv.data.get("name_attempts", 0) + 1
        conv.data["name_attempts"] = tries
        ok = K10.name_matches(text, on_record)
        turn.add("TOOL_RESULT", "name_check", {"attempt": tries, "match": ok}, self._iso())
        if not ok:
            if tries >= K10.max_name_attempts():
                self._handoff(conv, turn, "NAME_NOT_VERIFIED", "NAME_FAILED",
                              summary=("CM-03 WhatsApp: name not confirmed after 2 attempts", "offer callback"))
            else:
                self._reply(conv, turn, "NAME_RETRY")
            return
        conv.data["first_name"] = K10.first_name(on_record)
        if K10.otp_step_up():
            acct = self.tools.lookup_account(turn, phone=conv.sender)
            sent = acct and self.tools.send_otp(turn, conv.conversation_id, conv.borrower_id, conv.sender, acct["phone"],
                                                conv.lang)
            conv.state = AWAIT_OTP
            self._reply(conv, turn, "OTP_PROMPT") if sent else self._handoff(conv, turn, "OTP_SEND_LIMIT", "OTP_LIMIT")
            return
        conv.verified, turn.verified = True, True
        conv.verified_at = self._iso()
        conv.state = S_VERIFIED
        turn.add("STATE", "VERIFIED", {"binds": "sender+conversation+account", "method": "registered_number+name"},
                 self._iso())
        self._reply(conv, turn, "VERIFIED")
        pending = conv.data.pop("pending", None)
        if pending:
            self._serve(conv, turn, pending.get("text") or "", pending.get("attachment"))

    def _park(self, conv: Conversation, text: str, attachment: Optional[dict], u: N.Understanding) -> None:
        """Remember a request made before verification; it is served right after the OTP succeeds."""
        if u.intent in (N.GREETING, N.THANKS, N.RESEND_OTP, N.LANG_CHANGE) and not attachment:
            return
        if text or attachment:
            conv.data["pending"] = {"text": text[:500], "attachment": attachment}

    # ---------------- STOP ----------------
    def _stop(self, conv: Conversation, turn: Turn) -> None:
        bid = conv.borrower_id
        if bid is None:
            acct = self.tools.lookup_account(turn, phone=conv.sender)
            bid = acct["borrower_id"] if acct else None
        if bid is not None:   # the registered number itself: honour at once, no OTP needed to opt out
            conv.borrower_id = bid
            self.tools.record_stop(turn, bid)
        else:
            turn.add("STATE", "STOP_UNREGISTERED", {"note": "no account on this number; nothing to suppress"}, self._iso())
        self._outcome(conv, turn, "STOP_HONOURED")
        self._reply(conv, turn, "STOP_ACK")
        conv.state = CLOSED

    # ---------------- after verification ----------------
    def _summary(self, conv: Conversation, turn: Turn) -> List[dict]:
        return self.tools.account_summary(turn, conv.borrower_id)

    def _serve(self, conv: Conversation, turn: Turn, text: str, attachment: Optional[dict]) -> None:
        loans = None
        named = N.extract_loan_ids(text)
        if named:
            own = {r["loan_id"].upper() for r in self.ledger._conn().execute(
                "SELECT loan_id FROM loans WHERE borrower_id=?", (conv.borrower_id,)).fetchall()}
            foreign = [i for i in named if i not in own and self.ledger._conn().execute(
                "SELECT 1 FROM loans WHERE upper(loan_id)=?", (i,)).fetchone()]
            if foreign:
                self._fraud(conv, turn, [Signal("CROSS_ACCOUNT", "verified borrower asked about another borrower's loan",
                                                {"loan_ids": len(foreign)})])
                return
        if attachment:
            self._claim(conv, turn, text, attachment)
            return
        if conv.state == AWAIT_UTR:
            if N.extract_reference(text) or re.search(r"\b(no|nahi|nhi|don'?t have|not have|pata nahi|yaad nahi)\b", text, re.I):
                self._claim(conv, turn, text, None)
                return
        if conv.state == AWAIT_PTP_DATE:
            got, _ = k09.extract_date(text, self.clock().date())
            if got:
                self._ptp(conv, turn, got)
                return
        safety = K10.safety_intent(text)
        if safety:
            self._end_conversation(conv, turn, safety)
            return
        if K10.is_foreclosure(text):   # K10 route_not_serve: no quote in chat
            got = self._oldest_due(conv, turn)
            tid = self.tools.create_ticket(turn, K10.FORECLOSURE, conv.borrower_id, got[1]["obligation_id"] if got else None,
                                           ("WhatsApp: foreclosure quote requested", "K10: routed, never served in chat"))
            self._handoff(conv, turn, K10.FORECLOSURE, "ROUTED", ticket=False, ticket_ref=tid or "-")
            return
        u = self.nlu.understand(text, self.clock(), verified=True)
        i = u.intent
        if i in (N.MENU_PICK,):
            i = N.MENU.get(u.menu_pick or "", k09.OTHER)
        if conv.state == AWAIT_PTP_DATE and i in (k09.OTHER, k09.NEEDS_TIME):
            asks = conv.data.get("ptp_asks", 1)
            if asks >= 2:
                self._needs_time_noted(conv, turn)
            else:
                conv.data["ptp_asks"] = asks + 1
                self._reply(conv, turn, "ASK_PTP_DATE", days=self.ptp_max_days)
            return
        conv.state = S_VERIFIED if conv.state in (AWAIT_PTP_DATE, AWAIT_UTR) else conv.state
        handler = {
            N.BALANCE: self._balance, N.PAY_LINK: self._pay_link, k09.CLAIMS_PAID: lambda c, t: self._claim(c, t, text, None),
            N.STATEMENT: lambda c, t: self._document(c, t, "statement"), N.NOC: self._noc, N.LOAN_DETAILS: self._loan_details,
            k09.NEEDS_TIME: self._needs_time, k09.CALLBACK: self._callback, k09.HARDSHIP: self._hardship,
            k09.DISPUTE: lambda c, t: self._routed(c, t, "DISPUTE"), N.WAIVER: lambda c, t: self._routed(c, t, "WAIVER_REQUEST"),
            N.COMPLAINT: lambda c, t: self._routed(c, t, "COMPLAINT"),
        }.get(i)
        if handler:
            conv.data.pop("unknown", None)
            handler(conv, turn)
            return
        if i == k09.PTP and u.agreed_date:
            self._ptp(conv, turn, date.fromisoformat(u.agreed_date))
            return
        if i == k09.WRONG_PERSON:
            self._outcome(conv, turn, "WRONG_NUMBER_CLOSED")
            self._reply(conv, turn, "WRONG_NUMBER")
            conv.state = CLOSED
            return
        if i == N.LANG_CHANGE:
            self._reply(conv, turn, "LANG_SET")
            return
        if i in (N.GREETING, N.RESEND_OTP):
            self._reply(conv, turn, "VERIFIED_MENU")
            return
        if i == N.THANKS:
            self._reply(conv, turn, "GOODBYE")
            conv.state = CLOSED
            return
        conv.data["unknown"] = conv.data.get("unknown", 0) + 1
        if conv.data["unknown"] >= 2:
            self._handoff(conv, turn, "UNKNOWN_AFTER_CLARIFY", "UNKNOWN_HANDOFF")
        else:
            self._reply(conv, turn, "CLARIFY")

    # ---------------- intent handlers ----------------
    def _open_loans(self, conv: Conversation, turn: Turn) -> List[dict]:
        return [l for l in self._summary(conv, turn) if l["outstanding_paise"] > 0]

    @staticmethod
    def _next_due(loan: dict) -> Optional[dict]:
        unpaid = [o for o in loan["obligations"] if o["outstanding_paise"] > 0]
        return min(unpaid, key=lambda o: o["due_date"]) if unpaid else None

    def _balance(self, conv: Conversation, turn: Turn) -> None:
        loans = self._open_loans(conv, turn)
        if not loans:
            all_loans = self._summary(conv, turn)
            self._reply(conv, turn, "NO_DUES", last4=_last4(all_loans[0]["loan_id"]) if all_loans else "0000")
            self._outcome(conv, turn, "NO_DUES")
            return
        for loan in loans[:3]:
            nxt = self._next_due(loan)
            overdue = sum(o["outstanding_paise"] for o in loan["obligations"] if o["outstanding_paise"] > 0 and o["dpd"] > 0)
            self._reply(conv, turn, "BALANCE", product=loan["product"], last4=_last4(loan["loan_id"]), overdue=_rupees(overdue),
                        emi=_rupees(nxt["outstanding_paise"]), due_date=_fmt_date(nxt["due_date"]),
                        outstanding=_rupees(loan["outstanding_paise"]))
        self._reply(conv, turn, "ANYTHING_ELSE")
        self._outcome(conv, turn, "BALANCE_SHARED")

    def _oldest_due(self, conv: Conversation, turn: Turn):
        best = None
        for loan in self._open_loans(conv, turn):
            nxt = self._next_due(loan)
            if nxt and (best is None or nxt["due_date"] < best[1]["due_date"]):
                best = (loan, nxt)
        return best

    def _pay_link(self, conv: Conversation, turn: Turn) -> None:
        got = self._oldest_due(conv, turn)
        if got is None:
            self._reply(conv, turn, "NO_DUES", last4="0000")
            self._outcome(conv, turn, "NO_DUES")
            return
        loan, ob = got
        link = self.tools.create_payment_link(turn, conv.borrower_id, ob["obligation_id"], ob["outstanding_paise"])
        self._reply(conv, turn, "PAY_LINK", amount=_rupees(ob["outstanding_paise"]), last4=_last4(loan["loan_id"]), link=link)
        self._outcome(conv, turn, "LINK_SENT")

    def _claim(self, conv: Conversation, turn: Turn, text: str, attachment: Optional[dict]) -> None:
        today = self.clock().date()
        ocr = (attachment or {}).get("ocr") or {}
        if attachment:
            starts = [l.get("disbursed_on") for l in self._summary(conv, turn) if l.get("disbursed_on")]
            loan_start = min(date.fromisoformat(s) for s in starts) if starts else None
            sigs = check_attachment(attachment, self.clock(), loan_start)
            if sigs:
                self._fraud(conv, turn, sigs, key=self._not_matched_key())
                return
        ref = N.extract_reference(text) or (str(ocr["reference"]) if ocr.get("reference") else None)
        amount = N.extract_amount_paise(text)
        if amount is None and ocr.get("amount_rupees") is not None:
            amount = int(round(float(ocr["amount_rupees"]) * 100))
        claimed_on = N.claimed_payment_date(text, today)
        if claimed_on is None and ocr.get("date"):
            claimed_on = date.fromisoformat(str(ocr["date"])[:10])
        got = self._oldest_due(conv, turn)
        ob_id = got[1]["obligation_id"] if got else None
        if not ref:
            if conv.state != AWAIT_UTR:
                conv.state = AWAIT_UTR
                self._reply(conv, turn, "ASK_UTR")
                return
            conv.state = S_VERIFIED
            self.tools.record_payment_claim(turn, conv.conversation_id, conv.borrower_id, ob_id, None, amount, claimed_on,
                                            "PENDING_NO_REF")
            self._reply(conv, turn, "CLAIM_NOREF")
            self._outcome(conv, turn, "CLAIM_RECORDED_NO_REF")
            return
        conv.state = S_VERIFIED
        chk = self.tools.check_payment(turn, conv.borrower_id, ref, amount, claimed_on)
        if chk.verdict == FAKE:
            self.tools.record_payment_claim(turn, conv.conversation_id, conv.borrower_id, ob_id, ref, amount, claimed_on,
                                            "FAKE_SUSPECT", chk.signal.kind)
            self._fraud(conv, turn, [chk.signal], key=self._not_matched_key())
            return
        if chk.verdict == ALREADY_APPLIED:
            p = chk.payment
            self._reply(conv, turn, "RECEIPT", amount=_rupees(p["amount_paise"]), last4=_last4(p["obligation_id"].rsplit("-", 1)[0]),
                        receipt=p["payment_id"], remaining="")
            self._outcome(conv, turn, "RECEIPT_RESENT")
            return
        if chk.verdict == REVERSED:
            self.tools.record_payment_claim(turn, conv.conversation_id, conv.borrower_id, ob_id, ref, amount, claimed_on,
                                            "REVERSED")
            self._handoff(conv, turn, "PAYMENT_REVERSED", "PAYMENT_REVERSED")
            return
        if chk.verdict == PENDING:
            self.tools.record_payment_claim(turn, conv.conversation_id, conv.borrower_id, ob_id, ref, amount, claimed_on,
                                            "PENDING")
            self._reply(conv, turn, "CLAIM_PENDING", ref=ref)
            self._outcome(conv, turn, "CLAIM_PENDING_RECHECK")
            return
        # SETTLED in the D7 feed: the only path that applies money to the spine
        self._apply_settled(conv, turn, ref, chk.feed, ob_id, amount, claimed_on)

    def _not_matched_key(self) -> str:
        """K10 payment_claim_flow: MISMATCH or EDITED -> no receipt, neutral message, route to supervisor."""
        return "PAY_NOT_MATCHED" if self.identity_mode == "k10" else "FRAUD_REFUSE"

    def _apply_settled(self, conv: Conversation, turn: Turn, ref: str, feed: dict, ob_id: Optional[str],
                       amount: Optional[int], claimed_on: Optional[date], claim_id: Optional[str] = None) -> bool:
        target = feed.get("obligation_id") or ob_id
        if target is None:
            self._handoff(conv, turn, "NO_OPEN_OBLIGATION", "UNKNOWN_HANDOFF")
            return False
        try:
            res = self.tools.apply_verified_payment(turn, target, feed["amount_paise"], ref)
        except (PaymentError, KeyError) as e:
            self._fraud(conv, turn, [Signal("REFERENCE_REUSED", "spine refused the payment", {"error": type(e).__name__})])
            return False
        if claim_id is None:
            self.tools.record_payment_claim(turn, conv.conversation_id, conv.borrower_id, target, ref, amount, claimed_on,
                                            "VERIFIED")
        left = self.ledger._conn().execute("SELECT amount_due_paise - amount_paid_paise FROM obligations WHERE obligation_id=?",
                                           (target,)).fetchone()[0]
        remaining = render("REMAINING_DUE", conv.lang, left=_rupees(left)) if left > 0 else ""
        self._reply(conv, turn, "RECEIPT", amount=_rupees(feed["amount_paise"]), last4=_last4(target.rsplit("-", 1)[0]),
                    receipt=res["payment_id"], remaining=remaining)
        self._outcome(conv, turn, "CLAIM_VERIFIED_RECEIPT")
        return True

    def _band(self, conv: Conversation, turn: Turn) -> str:
        got = self._oldest_due(conv, turn)
        return str(got[0]["dpd_band"]) if got else "B1"

    def _ptp(self, conv: Conversation, turn: Turn, when: date) -> None:
        today = self.clock().date()
        offset = (when - today).days
        got = self._oldest_due(conv, turn)
        if got is None:
            self._reply(conv, turn, "NO_DUES", last4="0000")
            self._outcome(conv, turn, "NO_DUES")
            conv.state = S_VERIFIED
            return
        loan, ob = got
        if offset < 0:
            conv.state = AWAIT_PTP_DATE
            self._reply(conv, turn, "ASK_PTP_DATE", days=self.ptp_max_days)
            return
        if offset > self.ptp_max_days and not conv.data.get("ptp_far_asked") and self.identity_mode != "k10":
            conv.data["ptp_far_asked"] = True
            conv.state = AWAIT_PTP_DATE
            self._reply(conv, turn, "PTP_TOO_FAR", max_date=(today + timedelta(days=self.ptp_max_days)).strftime("%d %b"))
            return
        conv.state = S_VERIFIED
        broken = self.ledger._conn().execute("SELECT broken_ptp FROM borrower_state WHERE borrower_id=?",
                                             (conv.borrower_id,)).fetchone()
        res = self.tools.create_ptp(turn, conv.borrower_id, ob["obligation_id"], str(loan["dpd_band"]), offset,
                                    broken[0] if broken else 0,
                                    (f"WhatsApp PTP for {when.isoformat()}", "Captured by CM-03 after OTP verification"))
        conv.data["obligation_id"] = ob["obligation_id"]
        when_s = when.strftime("%d %b %Y")
        if res["action"] == "RECORD_PTP_NO_HOLD" or res["action"] == "VERIFY_PAYMENT":
            link = self.tools.create_payment_link(turn, conv.borrower_id, ob["obligation_id"], ob["outstanding_paise"])
            self._reply(conv, turn, "PTP_NO_HOLD", date=when_s, link=link)
            self._outcome(conv, turn, "PTP_RECORDED_NO_HOLD")
            return
        if offset > self.ptp_max_days:   # K13: beyond auto-accept -> supervisor approves or counters
            self._handoff(conv, turn, "PTP_BEYOND_WINDOW",
                          "PTP_CAPTURED_REVIEW" if self.identity_mode == "k10" else "PTP_REFERRED",
                          ticket=False, date=when_s, days=self.ptp_max_days)
            return
        link = self.tools.create_payment_link(turn, conv.borrower_id, ob["obligation_id"], ob["outstanding_paise"])
        self._reply(conv, turn, "PTP_CONFIRM", date=when_s, link=link)
        self._outcome(conv, turn, "PTP_RECORDED")

    def _broken_ptps(self, conv: Conversation) -> int:
        r = self.ledger._conn().execute("SELECT broken_ptp FROM borrower_state WHERE borrower_id=?",
                                        (conv.borrower_id,)).fetchone()
        return r[0] if r else 0

    def _needs_time(self, conv: Conversation, turn: Turn) -> None:
        threshold = ((self.policy.exceptions.get("promise_to_pay") or {}).get("broken_ptp") or {}).get("threshold")
        if threshold is not None and self._broken_ptps(conv) >= threshold:
            # K13 broken_ptp at/above threshold: do not solicit a PTP; push payment now with a link.
            got = self._oldest_due(conv, turn)
            if got is not None:
                loan, ob = got
                link = self.tools.create_payment_link(turn, conv.borrower_id, ob["obligation_id"], ob["outstanding_paise"])
                self._reply(conv, turn, "BROKEN_PTP_PUSH", link=link)
                self._outcome(conv, turn, "LINK_SENT")
                return
        conv.state = AWAIT_PTP_DATE
        conv.data["ptp_asks"] = 1
        self._reply(conv, turn, "ASK_PTP_DATE", days=self.ptp_max_days)

    def _needs_time_noted(self, conv: Conversation, turn: Turn) -> None:
        conv.state = S_VERIFIED
        got = self._oldest_due(conv, turn)
        if got is None:
            self._reply(conv, turn, "NO_DUES", last4="0000")
            self._outcome(conv, turn, "NO_DUES")
            return
        loan, ob = got
        if str(loan["dpd_band"]) in (self.policy.exceptions.get("needs_time") or {}).get("max_rebuttals_by_band", {}):
            self.tools.k13_exception(turn, conv.borrower_id, "NEEDS_TIME", str(loan["dpd_band"]), ob["obligation_id"],
                                     ("WhatsApp: needs time, no date given", "K13 needs_time, no hold"))
        link = self.tools.create_payment_link(turn, conv.borrower_id, ob["obligation_id"], ob["outstanding_paise"])
        self._reply(conv, turn, "NEEDS_TIME_NOTED", link=link)
        self._outcome(conv, turn, "NEEDS_TIME_NOTED")

    def _document(self, conv: Conversation, turn: Turn, kind: str) -> None:
        loans = self._summary(conv, turn)
        loan = next((l for l in loans if l["outstanding_paise"] > 0), loans[0])
        link = self.tools.get_document(turn, conv.borrower_id, kind, loan["loan_id"])
        self._reply(conv, turn, "STATEMENT", last4=_last4(loan["loan_id"]), link=link)
        self._outcome(conv, turn, "DOC_SENT")

    def _noc(self, conv: Conversation, turn: Turn) -> None:
        loans = self._summary(conv, turn)
        closed = [l for l in loans if l["status"] == "closed"]
        if closed:
            link = self.tools.get_document(turn, conv.borrower_id, "noc", closed[0]["loan_id"])
            self._reply(conv, turn, "NOC_READY", last4=_last4(closed[0]["loan_id"]), link=link)
            self._outcome(conv, turn, "NOC_SENT")
            return
        loan = loans[0]
        emis = sum(1 for o in loan["obligations"] if o["outstanding_paise"] > 0)
        self._reply(conv, turn, "NOC_NOT_YET", last4=_last4(loan["loan_id"]), outstanding=_rupees(loan["outstanding_paise"]),
                    emis=emis)
        self._outcome(conv, turn, "NOC_INFO")

    def _loan_details(self, conv: Conversation, turn: Turn) -> None:
        for loan in self._summary(conv, turn)[:3]:
            nxt = self._next_due(loan)
            emi = loan["obligations"][0]["amount_due_paise"] if loan["obligations"] else 0
            self._reply(conv, turn, "LOAN_DETAILS", product=loan["product"], last4=_last4(loan["loan_id"]),
                        principal=_rupees(loan["principal_paise"]), emi=_rupees(emi),
                        emis=sum(1 for o in loan["obligations"] if o["outstanding_paise"] > 0),
                        due_date=_fmt_date(nxt["due_date"]) if nxt else "-")
        self._outcome(conv, turn, "LOAN_DETAILS_SHARED")

    def _callback(self, conv: Conversation, turn: Turn) -> None:
        got = self._oldest_due(conv, turn)
        res = self.tools.k13_exception(turn, conv.borrower_id, "CALLBACK_REQUEST", self._band(conv, turn),
                                       got[1]["obligation_id"] if got else None,
                                       ("WhatsApp: borrower asked for a call back", "K13 callback, 2 working-hour SLA"))
        self._handoff(conv, turn, "CALLBACK_REQUESTED", "CALLBACK", ticket=not res.get("ticket_id"))

    def _hardship(self, conv: Conversation, turn: Turn) -> None:
        got = self._oldest_due(conv, turn)
        ob = got[1]["obligation_id"] if got else None
        res = self.tools.k13_exception(turn, conv.borrower_id, "HARDSHIP", self._band(conv, turn), ob,
                                       ("WhatsApp: borrower reported hardship", "K13 hardship hold; supervisor follow-up"))
        conv.data["obligation_id"] = ob
        self._handoff(conv, turn, "HARDSHIP", "HARDSHIP", ticket=not res.get("ticket_id"))

    def _routed(self, conv: Conversation, turn: Turn, event: str) -> None:
        got = self._oldest_due(conv, turn)
        res = self.tools.k13_exception(turn, conv.borrower_id, event, self._band(conv, turn),
                                       got[1]["obligation_id"] if got else None,
                                       (f"WhatsApp: borrower raised {event.lower().replace('_', ' ')}",
                                        "K13 routed exception: acknowledge, no commitment"))
        self._handoff(conv, turn, event, "ROUTED", ticket=False, ticket_ref=res.get("ticket_id") or "-")

    # ------------------------------------------------------------------ D7 recheck (E14)
    def recheck_pending_claims(self) -> List[dict]:
        """Re-check PENDING claims against the D7 feed: settled -> apply + receipt to the conversation;
        still absent past T+2 -> fraud suspect + ticket. Returns what changed."""
        out = []
        rows = self.ledger._conn().execute("SELECT * FROM cm03_claims WHERE status='PENDING' AND reference IS NOT NULL").fetchall()
        for r in rows:
            conv = self.get(r["conversation_id"])
            if conv is None:
                continue
            with self._lock_for(conv.sender):
                conv = self.get(r["conversation_id"])
                turn = Turn(conv.conversation_id, conv.verified)
                claimed = date.fromisoformat(r["claimed_date"]) if r["claimed_date"] else None
                chk = self.tools.check_payment(turn, conv.borrower_id, r["reference"], r["amount_paise"], claimed)
                new = None
                if chk.verdict == SETTLED:
                    if self._apply_settled(conv, turn, r["reference"], chk.feed, r["obligation_id"], r["amount_paise"],
                                           claimed, claim_id=r["claim_id"]):
                        new = "VERIFIED"
                elif chk.verdict == FAKE:
                    self._fraud(conv, turn, [chk.signal])
                    new = "FAKE_SUSPECT"
                elif chk.verdict == REVERSED:
                    self._handoff(conv, turn, "PAYMENT_REVERSED", "PAYMENT_REVERSED")
                    new = "REVERSED"
                if new:
                    with self.ledger.tx() as c:
                        c.execute("UPDATE cm03_claims SET status=?, updated_at=? WHERE claim_id=?", (new, self._iso(), r["claim_id"]))
                    self._save(conv, turn, None)
                    out.append({"claim_id": r["claim_id"], "conversation_id": conv.conversation_id, "status": new})
        return out

    # ------------------------------------------------------------------ metrics
    def metrics(self) -> dict:
        c = self.ledger._conn()
        rows = [self._row_to_conv(r) for r in c.execute("SELECT * FROM cm03_conversations").fetchall()]
        n = len(rows)
        resolved = sum(1 for r in rows if r.resolved)
        reasons: Dict[str, int] = {}
        for r in rows:
            if r.handoff_reason:
                reasons[r.handoff_reason] = reasons.get(r.handoff_reason, 0) + 1
        lat = sorted(r.first_reply_ms for r in rows if r.first_reply_ms is not None)
        pct = lambda p: lat[min(len(lat) - 1, int(round(p * (len(lat) - 1))))] if lat else None  # noqa: E731
        guard = c.execute("SELECT COUNT(*) FROM cm03_timeline WHERE kind='GUARD_BLOCK'").fetchone()[0]
        tools = dict(c.execute("SELECT name, COUNT(*) FROM cm03_timeline WHERE kind='TOOL_CALL' GROUP BY name").fetchall())
        return {"provenance": PROVENANCE, "conversations": n, "resolved_without_human": resolved,
                "resolution_rate": round(resolved / n, 4) if n else None, "handoff_reasons": reasons,
                "fraud_suspects": sum(1 for r in rows if r.fraud), "verified": sum(1 for r in rows if r.verified),
                "first_reply_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": lat[-1] if lat else None,
                                   "over_30s": sum(1 for x in lat if x > 30_000)},
                "guard_blocks_before_verification": guard, "tool_calls": tools}


def _att_summary(att: Optional[dict]) -> Optional[dict]:
    if not att:
        return None
    return {"type": att.get("type", "image"), "kind": att.get("kind"), "has_ocr": bool(att.get("ocr"))}
