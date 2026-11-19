"""CM-03 tables, kept in the same SQLite file as the K03 spine and K06 ledger.

The spine stays the source of truth for borrowers, loans, EMIs and verified payments. These tables
hold only the conversation layer: sessions, the CRM timeline (envelopes), hashed OTPs, payment
claims, payment links, and the synthetic D7 bank feed used in place of a real settlement feed.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Optional

from ..safejson import finite

CM03_SCHEMA = """
CREATE TABLE IF NOT EXISTS cm03_conversations (
  conversation_id TEXT PRIMARY KEY, sender TEXT NOT NULL, sender_masked TEXT NOT NULL, borrower_id TEXT,
  state TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'en', verified INTEGER NOT NULL DEFAULT 0, verified_at TEXT,
  data TEXT NOT NULL DEFAULT '{}', outcomes TEXT NOT NULL DEFAULT '[]', handoff_reason TEXT,
  fraud INTEGER NOT NULL DEFAULT 0, turns INTEGER NOT NULL DEFAULT 0, first_reply_ms REAL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cm03_conv_sender ON cm03_conversations (sender, updated_at);
CREATE TABLE IF NOT EXISTS cm03_timeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT, provenance TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cm03_tl_conv ON cm03_timeline (conversation_id, id);
CREATE TABLE IF NOT EXISTS cm03_inbound_ids (
  message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, replies TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cm03_otps (
  otp_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, borrower_id TEXT NOT NULL, sender TEXT NOT NULL,
  code_hash TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cm03_otp_conv ON cm03_otps (conversation_id, created_at);
CREATE TABLE IF NOT EXISTS cm03_lockouts (
  borrower_id TEXT PRIMARY KEY, until TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS cm03_claims (
  claim_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, borrower_id TEXT NOT NULL, obligation_id TEXT,
  reference TEXT, reference_key TEXT, amount_paise INTEGER, claimed_date TEXT, status TEXT NOT NULL,
  reason TEXT, payment_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cm03_claim_ref ON cm03_claims (reference_key);
CREATE TABLE IF NOT EXISTS cm03_links (
  token TEXT PRIMARY KEY, kind TEXT NOT NULL, borrower_id TEXT NOT NULL, obligation_id TEXT,
  amount_paise INTEGER, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
-- Synthetic D7 settlement feed (SIMULATED stand-in for the bank / LMS feed). Trusted source for
-- "was this money actually received"; a borrower's claim is only ever checked against it.
CREATE TABLE IF NOT EXISTS cm03_bank_feed (
  reference TEXT PRIMARY KEY, reference_key TEXT NOT NULL UNIQUE, amount_paise INTEGER NOT NULL,
  credited_on TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('SETTLED','PENDING','REVERSED')),
  borrower_id TEXT, obligation_id TEXT);
"""


def reference_key(reference: Any) -> str:
    """Duplicate-check key for a bank reference: NFKC (full-width -> ASCII), drop whitespace and
    invisible format characters, casefold. Same rule as spine.ledger.reference_key on the
    redteam/fix-high branch (RT-F06); the spine's own function is used when present."""
    try:  # prefer the spine's normalised key when that fix is merged
        from spine.ledger import reference_key as spine_key  # type: ignore
        return spine_key(reference)
    except ImportError:
        pass
    text = unicodedata.normalize("NFKC", str(reference))
    text = "".join(ch for ch in text if not ch.isspace() and unicodedata.category(ch) != "Cf")
    return unicodedata.normalize("NFKC", text.casefold())


_RX_OTPISH = re.compile(r"(?<!\d)\d{6}(?!\d)")
_RX_PHONE = re.compile(r"(\+\d[\d\s-]{9,14}\d|(?<!\d)[06-9]\d{9}(?!\d))")


def mask_phone(number: Optional[str]) -> str:
    """'+910000012345' -> '+********2345' (last 4 digits only)."""
    if not number:
        return "<none>"
    digits = re.sub(r"\D", "", number)
    if len(digits) <= 4:
        return "*" * len(digits)
    return ("+" if str(number).strip().startswith("+") else "") + "*" * (len(digits) - 4) + digits[-4:]


def scrub(text: Optional[str]) -> str:
    """What may be written to the timeline / logs: 6-digit codes (OTPs) and phone numbers masked."""
    if not text:
        return ""
    t = _RX_PHONE.sub(lambda m: mask_phone(m.group(1)), text)
    return _RX_OTPISH.sub("******", t)


def dumps(obj: Any) -> str:
    return json.dumps(finite(obj), default=str, ensure_ascii=False, sort_keys=True, allow_nan=False)


def init_schema(conn) -> None:
    conn.executescript(CM03_SCHEMA)
