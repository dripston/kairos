"""Synthetic OTP service for the CM-03 identity gate (E11).

* 6 digits, valid 5 minutes, 3 attempts per code; the 3rd wrong attempt locks the borrower for
  30 minutes (across conversations, so a brute-forcer cannot just open a new chat) and the
  engine hands the conversation to a human.
* Only an HMAC-SHA256 of the code is stored (keyed by a server secret and the otp id); the code
  itself exists only in the WhatsApp message sent to the REGISTERED number and is never written
  to the timeline, the audit log or any log line.
* A code is bound to (conversation, borrower, sender): a code from another conversation, another
  sender or an already-used code never verifies (replay).
* At most 3 codes per conversation (initial + 2 resends).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

OTP_DIGITS = 6
OTP_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 3
MAX_SENDS = 3
LOCKOUT = timedelta(minutes=30)

VERIFIED, WRONG, EXPIRED, LOCKED, NO_OTP = "VERIFIED", "WRONG", "EXPIRED", "LOCKED", "NO_OTP"


def server_secret() -> bytes:
    """KAIROS_CM03_SECRET in the environment; a per-process random key otherwise (dev/demo)."""
    global _SECRET
    if _SECRET is None:
        env = os.environ.get("KAIROS_CM03_SECRET")
        _SECRET = env.encode() if env else secrets.token_bytes(32)
    return _SECRET


_SECRET: Optional[bytes] = None


@dataclass
class VerifyResult:
    status: str
    attempts_left: int = 0


class OtpService:
    def __init__(self, ledger, clock: Callable[[], datetime], code_source: Optional[Callable[[], str]] = None):
        self.ledger = ledger
        self.clock = clock
        self._code_source = code_source or (lambda: f"{secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}")

    @staticmethod
    def _hash(otp_id: str, code: str) -> str:
        return hmac.new(server_secret(), f"{otp_id}:{code}".encode(), hashlib.sha256).hexdigest()

    def _iso(self, dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    def locked_until(self, borrower_id: str) -> Optional[datetime]:
        r = self.ledger._conn().execute("SELECT until FROM cm03_lockouts WHERE borrower_id=?", (borrower_id,)).fetchone()
        if r and datetime.fromisoformat(r["until"]) > self.clock():
            return datetime.fromisoformat(r["until"])
        return None

    def sends(self, conversation_id: str) -> int:
        return self.ledger._conn().execute("SELECT COUNT(*) FROM cm03_otps WHERE conversation_id=?",
                                           (conversation_id,)).fetchone()[0]

    def issue(self, conversation_id: str, borrower_id: str, sender: str) -> Optional[str]:
        """New code for this conversation (older active codes are superseded). Returns the plain code
        for the adapter to deliver, or None when sends are exhausted or the borrower is locked."""
        if self.locked_until(borrower_id) or self.sends(conversation_id) >= MAX_SENDS:
            return None
        code = self._code_source()
        otp_id = "OTP-" + uuid.uuid4().hex[:12]
        now = self.clock()
        with self.ledger.tx() as c:
            c.execute("UPDATE cm03_otps SET status='SUPERSEDED' WHERE conversation_id=? AND status='ACTIVE'",
                      (conversation_id,))
            c.execute("INSERT INTO cm03_otps (otp_id, conversation_id, borrower_id, sender, code_hash, expires_at,"
                      " attempts, status, created_at) VALUES (?,?,?,?,?,?,0,'ACTIVE',?)",
                      (otp_id, conversation_id, borrower_id, sender, self._hash(otp_id, code),
                       self._iso(now + OTP_TTL), self._iso(now)))
        return code

    def verify(self, conversation_id: str, borrower_id: str, sender: str, code: str) -> VerifyResult:
        now = self.clock()
        if self.locked_until(borrower_id):
            return VerifyResult(LOCKED)
        with self.ledger.tx() as c:
            r = c.execute("SELECT * FROM cm03_otps WHERE conversation_id=? AND status='ACTIVE' ORDER BY created_at DESC"
                          " LIMIT 1", (conversation_id,)).fetchone()
            if r is None or r["borrower_id"] != borrower_id or r["sender"] != sender:
                return VerifyResult(NO_OTP)
            if datetime.fromisoformat(r["expires_at"]) <= now:
                c.execute("UPDATE cm03_otps SET status='EXPIRED' WHERE otp_id=?", (r["otp_id"],))
                return VerifyResult(EXPIRED)
            if hmac.compare_digest(r["code_hash"], self._hash(r["otp_id"], str(code).strip())):
                c.execute("UPDATE cm03_otps SET status='USED', attempts=attempts+1 WHERE otp_id=?", (r["otp_id"],))
                return VerifyResult(VERIFIED)
            attempts = r["attempts"] + 1
            if attempts >= MAX_ATTEMPTS:
                c.execute("UPDATE cm03_otps SET status='LOCKED', attempts=? WHERE otp_id=?", (attempts, r["otp_id"]))
                c.execute("INSERT INTO cm03_lockouts (borrower_id, until, reason) VALUES (?,?,?) ON CONFLICT(borrower_id)"
                          " DO UPDATE SET until=excluded.until, reason=excluded.reason",
                          (borrower_id, self._iso(now + LOCKOUT), "OTP_ATTEMPTS"))
                return VerifyResult(LOCKED)
            c.execute("UPDATE cm03_otps SET attempts=? WHERE otp_id=?", (attempts, r["otp_id"]))
            return VerifyResult(WRONG, MAX_ATTEMPTS - attempts)
