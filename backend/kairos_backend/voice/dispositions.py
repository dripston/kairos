"""K18 unified disposition schema: the post-call variables every Kairos Sarvam bot returns.

    parse_disposition(raw_variables) -> Disposition      (strict; never raises on bad bot output)

Validation is fail-closed:
  * unknown / missing `disposition`              -> UNCLEAR with confidence 0 (human review)
  * `disposition_confidence` not a number in 0..1 -> 0.0 (the platform may send it as a string)
  * `ptp_date` not YYYY-MM-DD / not a real date   -> "" ; `callback_at` not ISO datetime -> ""
  * other enums unknown                           -> UNKNOWN / NONE (OTHER for a HARDSHIP call)
  * `call_summary` / `rule_path`                  -> phone numbers, emails, digit runs and amounts scrubbed

Three facts stay separate (CLAUDE.md): provider delivery (call connected) is reach; the disposition is
borrower intent; a payment is only ever what the ledger verified (D7/LMS).

Report ordinal (PRD): No data < Sent < Delivered < Read < Clicked < Responded. For a voice call:
  no call record -> NO_DATA, dialled but not connected -> SENT, connected (short / voicemail) ->
  DELIVERED, connected and heard (> K01 pickup threshold) without a usable response -> READ,
  a borrower disposition -> RESPONDED. CLICKED has no voice equivalent (a click is reach only, K01).
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional

DISPOSITIONS = ("PTP", "PAY_NOW_LINK", "ALREADY_PAID", "PARTIAL_PTP", "NEEDS_TIME_NO_DATE", "CALLBACK_REQUESTED",
                "HARDSHIP", "DISPUTE", "HUMAN_REQUESTED", "STOP_REQUESTED", "WRONG_PERSON", "THIRD_PARTY",
                "REFUSED_TO_PAY", "LANGUAGE_BARRIER", "ABUSIVE", "NO_RESPONSE")
UNCLEAR = "UNCLEAR"   # Kairos-internal: the bot's value was not in the enum (never sent by a bot)
PTP_AMOUNT_TYPES = ("FULL", "PARTIAL", "UNKNOWN")
HARDSHIP_TYPES = ("MEDICAL", "JOB_LOSS", "BUSINESS_LOSS", "DEATH_IN_FAMILY", "NATURAL_DISASTER", "OTHER", "NONE")
POST_CALL_VARIABLES = ("disposition", "disposition_confidence", "ptp_date", "ptp_amount_type", "callback_at",
                       "hardship_type", "language_used", "needs_human", "call_summary", "rule_path")

# PRD reporting ordinal
REPORT_LEVELS = ("NO_DATA", "SENT", "DELIVERED", "READ", "CLICKED", "RESPONDED")
REPORT_LABELS = {"NO_DATA": "No data", "SENT": "Sent", "DELIVERED": "Delivered", "READ": "Read",
                 "CLICKED": "Clicked", "RESPONDED": "Responded"}
NON_RESPONSES = ("NO_RESPONSE", UNCLEAR)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PHONE = re.compile(r"\+?\d[\d \-]{7,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MONEY = re.compile(r"(₹|\brs\.?|\binr\b|\brupees?\b)\s*[\d,.]+|[\d,.]+\s*(₹|\brs\b|\binr\b|\brupees?\b)", re.I)
_DIGITS = re.compile(r"\d{3,}")
SUMMARY_MAX = 280


def scrub_text(text: Any, limit: int = SUMMARY_MAX) -> str:
    """No PII / money in stored free text: phones, emails, amounts and any 3+ digit run are masked."""
    t = "" if text is None else str(text)
    t = _EMAIL.sub("[email]", t)
    t = _MONEY.sub("[amount]", t)
    t = _PHONE.sub("[number]", t)
    t = _DIGITS.sub("[number]", t)
    return re.sub(r"\s+", " ", t).strip()[:limit]


def parse_confidence(v: Any) -> float:
    """Float in [0, 1]; strings accepted ('0.82'); anything else (or NaN / out of range) -> 0.0."""
    if isinstance(v, bool):
        return 0.0
    try:
        f = float(str(v).strip()) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or f < 0 or f > 1:
        return 0.0
    return f


def parse_date(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    if not _DATE.match(s):
        return ""
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return ""


def parse_datetime(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    if not s or len(s) < 16 or "T" not in s.replace(" ", "T"):
        return ""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ""


def parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "y")


def _enum(v: Any, allowed, fallback: str) -> str:
    s = "" if v is None else str(v).strip().upper().replace(" ", "_").replace("-", "_")
    return s if s in allowed else fallback


@dataclass
class Disposition:
    disposition: str                 # one of DISPOSITIONS, or UNCLEAR
    confidence: float
    ptp_date: str = ""               # YYYY-MM-DD or ""
    ptp_amount_type: str = "UNKNOWN"
    callback_at: str = ""            # ISO datetime or ""
    hardship_type: str = "NONE"
    language_used: str = ""
    needs_human: bool = False
    call_summary: str = ""
    rule_path: str = ""
    raw_disposition: str = ""        # what the bot sent (scrubbed), for audit
    errors: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def parse_disposition(raw: Optional[dict]) -> Disposition:
    raw = raw or {}
    errors = []
    raw_disp = scrub_text(raw.get("disposition"), 40)
    disp = _enum(raw.get("disposition"), DISPOSITIONS, UNCLEAR)
    conf = parse_confidence(raw.get("disposition_confidence"))
    if disp == UNCLEAR:
        errors.append("UNKNOWN_DISPOSITION")
        conf = 0.0
    elif conf == 0.0 and str(raw.get("disposition_confidence", "")).strip() not in ("0", "0.0"):
        errors.append("BAD_CONFIDENCE")
    ptp_date = parse_date(raw.get("ptp_date"))
    if raw.get("ptp_date") not in (None, "") and not ptp_date:
        errors.append("BAD_PTP_DATE")
    callback_at = parse_datetime(raw.get("callback_at"))
    if raw.get("callback_at") not in (None, "") and not callback_at:
        errors.append("BAD_CALLBACK_AT")
    hardship = _enum(raw.get("hardship_type"), HARDSHIP_TYPES, "OTHER" if disp == "HARDSHIP" else "NONE")
    lang = re.sub(r"[^A-Za-z -]", "", str(raw.get("language_used") or ""))[:30].strip()
    return Disposition(
        disposition=disp, confidence=conf, ptp_date=ptp_date,
        ptp_amount_type=_enum(raw.get("ptp_amount_type"), PTP_AMOUNT_TYPES, "UNKNOWN"),
        callback_at=callback_at, hardship_type=hardship, language_used=lang,
        needs_human=parse_bool(raw.get("needs_human")), call_summary=scrub_text(raw.get("call_summary")),
        rule_path=scrub_text(raw.get("rule_path"), 200), raw_disposition=raw_disp, errors=errors)


def report_level(call_record: bool, connected: Optional[bool], duration_seconds: Optional[float],
                 disposition: Optional[str], pickup_threshold_s: float = 10) -> tuple[int, str]:
    """(ordinal, PRD label) for reports. Never upgrades on intent the bot did not capture."""
    if not call_record:
        level = "NO_DATA"
    elif disposition and disposition not in NON_RESPONSES:
        level = "RESPONDED"
    elif connected:
        heard = duration_seconds is not None and duration_seconds > pickup_threshold_s
        level = "READ" if heard else "DELIVERED"
    else:
        level = "SENT"
    return REPORT_LEVELS.index(level), REPORT_LABELS[level]
