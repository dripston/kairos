"""The tools the CM-03 engine calls. Each call is real code against the K03 spine / K06 ledger /
CM-03 tables (no mocks) and leaves a TOOL_CALL envelope on the CRM timeline with its provenance,
redacted arguments, a short result and the time it took. OTP codes never reach an envelope.

    lookup_account        spine: who owns this registered number / loan id
    account_summary       spine: loans, EMIs, outstanding (borrower_summary), only after OTP
    send_otp / verify_otp OtpService + WhatsApp adapter (code goes to the REGISTERED number only)
    create_payment_link   synthetic link with an HMAC-signed token
    get_document          synthetic statement / NOC link (HMAC token)
    record_payment_claim  claim row (never marks anything paid)
    check_payment         read-only check of a reference against the spine + synthetic D7 feed
    apply_verified_payment K06 Ledger.record_payment_verified -> K03 apply_payment (D7-settled only)
    create_ptp            K06 Ledger.apply_exception(PTP_OFFERED) -> K13 PTP hold
    record_stop           K06 Ledger.record_stop -> every channel opted out
    create_ticket         K13 routed exception via apply_exception, or a direct supervisor ticket
    send_whatsapp_reply   WhatsApp adapter (dry-run: nothing leaves the machine)
"""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional

from spine.ledger import PaymentError, borrower_summary

from ..exceptions import ExceptionEvent
from . import PROVENANCE
from .fraud import RefCheck, check_reference
from .otp import OtpService, VerifyResult, server_secret
from .store import dumps, mask_phone, reference_key

LINK_TTL = timedelta(hours=24)
PAY_BASE = "https://pay.kairos.example/p/"
DOC_BASE = "https://docs.kairos.example/d/"


class DryRunWhatsAppAdapter:
    """WhatsApp Cloud API stand-in. dry_run is always on here: send() builds the message, keeps it in
    a bounded in-memory outbox per recipient (the SIMULATED phone that eval/demo borrowers read their
    OTP from) and returns DRY_RUN. Logs and envelopes only ever see the masked number.
    The K07 phone-demo adapter (branch k07-k08/channel-adapters) sends approved templates only; free
    text session replies are not wired to it, so CM-03 stays dry-run."""

    provenance = PROVENANCE

    def __init__(self, keep: int = 20):
        self._lock = threading.Lock()
        self._outbox: Dict[str, Deque[dict]] = collections.defaultdict(lambda: collections.deque(maxlen=keep))
        self.sent = 0

    def send(self, to: str, text: str, kind: str = "text") -> dict:
        ref = "wamid.SIM-" + uuid.uuid4().hex[:16]
        with self._lock:
            self._outbox[to].append({"text": text, "kind": kind, "ref": ref})
            self.sent += 1
        return {"status": "DRY_RUN", "provider_ref": ref}

    def inbox(self, number: str) -> List[dict]:
        """What the (simulated) phone at `number` has received. Demo / eval only."""
        with self._lock:
            return list(self._outbox.get(number, ()))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign_token(payload: dict) -> str:
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = _b64(hmac.new(server_secret(), body.encode(), hashlib.sha256).digest()[:16])
    return f"{body}.{sig}"


def verify_token(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(server_secret(), body.encode(), hashlib.sha256).digest()[:16])
        if not hmac.compare_digest(sig, good):
            return None
        pad = "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(body + pad))
    except (ValueError, json.JSONDecodeError):
        return None


class Turn:
    """Collects the envelopes of one inbound message; the engine flushes them in one transaction."""

    def __init__(self, conversation_id: str, verified: bool):
        self.conversation_id = conversation_id
        self.verified = verified
        self.envelopes: List[dict] = []
        self.replies: List[dict] = []

    def add(self, kind: str, name: str, payload: dict, ts: str) -> None:
        self.envelopes.append({"kind": kind, "name": name, "payload": payload, "ts": ts, "verified": int(self.verified),
                               "provenance": PROVENANCE})


class ToolBox:
    def __init__(self, ledger, clock: Callable[[], datetime], adapter: Optional[DryRunWhatsAppAdapter] = None,
                 otp_code_source: Optional[Callable[[], str]] = None):
        self.ledger = ledger
        self.clock = clock
        self.adapter = adapter or DryRunWhatsAppAdapter()
        self.otp = OtpService(ledger, clock, otp_code_source)
        self.calls = collections.Counter()

    # ---- plumbing ----
    def _iso(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def _call(self, turn: Optional[Turn], name: str, args: dict, fn: Callable[[], Any], summarize: Callable[[Any], dict]):
        t0 = time.perf_counter()
        ok, err, result = True, None, None
        try:
            result = fn()
            return result
        except Exception as e:  # recorded on the timeline, then re-raised to the engine
            ok, err = False, f"{type(e).__name__}: {str(e)[:120]}"
            raise
        finally:
            self.calls[name] += 1
            if turn is not None:
                turn.add("TOOL_CALL", name, {"args": args, "ok": ok, "error": err,
                                             "result": summarize(result) if ok else None,
                                             "ms": round((time.perf_counter() - t0) * 1000, 2)}, self._iso())

    # ---- tools ----
    def lookup_account(self, turn: Turn, phone: Optional[str] = None, loan_id: Optional[str] = None) -> Optional[dict]:
        def run():
            c = self.ledger._conn()
            if phone:
                r = c.execute("SELECT borrower_id, phone, language FROM borrowers WHERE phone=? ORDER BY borrower_id LIMIT 1",
                              (phone,)).fetchone()
            else:
                r = c.execute("SELECT b.borrower_id, b.phone, b.language FROM loans l JOIN borrowers b ON"
                              " b.borrower_id=l.borrower_id WHERE upper(l.loan_id)=upper(?)", (loan_id,)).fetchone()
            return dict(r) if r else None
        return self._call(turn, "lookup_account", {"by": "phone" if phone else "loan_id"}, run,
                          lambda r: {"found": r is not None})

    def account_summary(self, turn: Turn, borrower_id: str) -> List[dict]:
        today = self.clock().date()
        return self._call(turn, "account_summary", {"borrower_id": borrower_id},
                          lambda: borrower_summary(self.ledger._spine(), borrower_id, as_of=today),
                          lambda r: {"loans": len(r)})

    def send_otp(self, turn: Turn, conversation_id: str, borrower_id: str, sender: str, registered_phone: str,
                 lang: str) -> bool:
        from .copy import render

        def run():
            code = self.otp.issue(conversation_id, borrower_id, sender)
            if code is None:
                return False
            self.adapter.send(registered_phone, render("OTP_MESSAGE", lang, code=code), kind="otp")
            return True
        return self._call(turn, "send_otp", {"to": mask_phone(registered_phone), "code": "******"}, run,
                          lambda r: {"sent": bool(r), "provenance": PROVENANCE})

    def verify_otp(self, turn: Turn, conversation_id: str, borrower_id: str, sender: str, code: str) -> VerifyResult:
        return self._call(turn, "verify_otp", {"code": "******"},
                          lambda: self.otp.verify(conversation_id, borrower_id, sender, code),
                          lambda r: {"status": r.status, "attempts_left": r.attempts_left})

    def _link(self, kind: str, borrower_id: str, obligation_id: Optional[str], amount_paise: Optional[int]) -> str:
        now = self.clock()
        payload = {"k": kind, "b": borrower_id, "o": obligation_id, "a": amount_paise,
                   "x": (now + LINK_TTL).isoformat(timespec="seconds"), "n": uuid.uuid4().hex[:8]}
        token = sign_token(payload)
        with self.ledger.tx() as c:
            c.execute("INSERT INTO cm03_links (token, kind, borrower_id, obligation_id, amount_paise, expires_at, created_at)"
                      " VALUES (?,?,?,?,?,?,?)", (token, kind, borrower_id, obligation_id, amount_paise, payload["x"],
                                                   now.isoformat(timespec="seconds")))
        return (PAY_BASE if kind == "pay" else DOC_BASE) + token

    def create_payment_link(self, turn: Turn, borrower_id: str, obligation_id: str, amount_paise: int) -> str:
        return self._call(turn, "create_payment_link", {"obligation_id": obligation_id, "amount_paise": amount_paise},
                          lambda: self._link("pay", borrower_id, obligation_id, amount_paise),
                          lambda r: {"link": "https://pay.kairos.example/p/…", "signed": "HMAC-SHA256",
                                     "provenance": PROVENANCE})

    def get_document(self, turn: Turn, borrower_id: str, kind: str, loan_id: str) -> str:
        return self._call(turn, "get_document", {"kind": kind, "loan_id": loan_id},
                          lambda: self._link(kind, borrower_id, None, None),
                          lambda r: {"link": "https://docs.kairos.example/d/…", "provenance": PROVENANCE})

    def check_payment(self, turn: Turn, borrower_id: str, reference: Optional[str], amount_paise: Optional[int],
                      claimed_on: Optional[date]) -> RefCheck:
        today = self.clock().date()
        return self._call(turn, "check_payment_d7", {"reference": reference, "amount_paise": amount_paise,
                                                      "claimed_on": claimed_on},
                          lambda: check_reference(self.ledger._conn(), reference, amount_paise, claimed_on, borrower_id, today),
                          lambda r: {"verdict": r.verdict, "signal": r.signal.kind if r.signal else None,
                                     "source": "D7 feed (SIMULATED)"})

    def record_payment_claim(self, turn: Turn, conversation_id: str, borrower_id: str, obligation_id: Optional[str],
                             reference: Optional[str], amount_paise: Optional[int], claimed_on: Optional[date],
                             status: str, reason: str = "") -> str:
        def run():
            cid = "CLM-" + uuid.uuid4().hex[:10]
            now = self._iso()
            with self.ledger.tx() as c:
                c.execute("INSERT INTO cm03_claims (claim_id, conversation_id, borrower_id, obligation_id, reference,"
                          " reference_key, amount_paise, claimed_date, status, reason, created_at, updated_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, conversation_id, borrower_id, obligation_id, reference,
                           reference_key(reference) if reference else None, amount_paise,
                           claimed_on.isoformat() if claimed_on else None, status, reason, now, now))
            return cid
        return self._call(turn, "record_payment_claim", {"reference": reference, "status": status}, run,
                          lambda r: {"claim_id": r, "marks_paid": False})

    def apply_verified_payment(self, turn: Turn, obligation_id: str, amount_paise: int, reference: str) -> dict:
        def run():
            pid = "PAY-CM03-" + reference_key(reference).upper()[:22]
            out = self.ledger.record_payment_verified(obligation_id, amount_paise, self.clock(), pid, reference, mode="upi")
            out["payment_id"] = pid
            return out
        return self._call(turn, "apply_verified_payment", {"obligation_id": obligation_id, "amount_paise": amount_paise,
                                                            "source": "D7 SETTLED"}, run,
                          lambda r: {k: r[k] for k in ("applied", "obligation_cleared", "canceled")})

    def create_ptp(self, turn: Turn, borrower_id: str, obligation_id: str, band: str, offset_days: int,
                   broken_ptp: int, summary: tuple) -> dict:
        ev = ExceptionEvent("PTP_OFFERED", band=band, date_offset_days=offset_days, broken_ptp_count=broken_ptp)
        return self._call(turn, "create_ptp", {"obligation_id": obligation_id, "offset_days": offset_days},
                          lambda: self.ledger.apply_exception(borrower_id, ev, self.clock(), obligation_id, summary),
                          lambda r: {k: r[k] for k in ("action", "hold", "ticket_id")})

    def record_stop(self, turn: Turn, borrower_id: str) -> int:
        return self._call(turn, "record_stop", {"borrower_id": borrower_id, "scope": "all channels"},
                          lambda: self.ledger.record_stop(borrower_id, self.clock(), channel="whatsapp"),
                          lambda r: {"canceled_queued": r})

    def k13_exception(self, turn: Turn, borrower_id: str, event: str, band: str, obligation_id: Optional[str],
                      summary: tuple, evidence: tuple = ()) -> dict:
        ev = ExceptionEvent(event, band=band)
        return self._call(turn, "create_ticket", {"type": event, "via": "K13 apply_exception"},
                          lambda: self.ledger.apply_exception(borrower_id, ev, self.clock(), obligation_id, summary, evidence),
                          lambda r: {"ticket_id": r.get("ticket_id"), "hold": r.get("hold"), "action": r.get("action")})

    def create_ticket(self, turn: Turn, type_: str, borrower_id: Optional[str], obligation_id: Optional[str],
                      summary: tuple, evidence: tuple = (), audit_type: Optional[str] = None) -> str:
        def run():
            queue = (self.ledger.policy.exceptions.get("queues") or {}).get("default", "COLLECTIONS_SUPERVISOR")
            with self.ledger.tx() as c:
                tid = self.ledger._ticket(c, queue, borrower_id or "UNKNOWN", obligation_id, type_, summary, self.clock(),
                                          evidence)
                self.ledger._audit(c, "agent:cm03", audit_type or f"HANDOFF_{type_}", borrower_id, at=self.clock(),
                                   reason=type_, ticket=tid, provenance=PROVENANCE)
            return tid
        return self._call(turn, "create_ticket", {"type": type_}, run, lambda r: {"ticket_id": r})

    def send_whatsapp_reply(self, turn: Turn, to: str, text: str) -> dict:
        return self._call(turn, "send_whatsapp_reply", {"to": mask_phone(to)}, lambda: self.adapter.send(to, text),
                          lambda r: {"status": r["status"]})


__all__ = ["ToolBox", "Turn", "DryRunWhatsAppAdapter", "sign_token", "verify_token", "PaymentError"]
