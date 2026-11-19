"""Deterministic, explainable fake / fraud detection for CM-03 (E11, E13, E16).

Every detector returns Signal(kind, reason, evidence). Any signal means: refuse, log a
FRAUD_SUSPECT envelope, open a supervisor ticket, hand off, and never disclose anything.

Text detectors run on every inbound message, before verification (they read only the message):
  PROMPT_INJECTION      "ignore previous instructions", "you are now ...", "system prompt", ...
  PHISHING_LINK         any URL that is not one of our own synthetic domains
  SPOOFED_LENDER        "I am from the bank / RBI / head office", "KYC expired, account blocked", ...
  OTP_SOCIAL_ENGINEERING asking for / to forward / to redirect the OTP
  OTHER_BORROWER_DATA   asks for a relative's / all customers' data
Account detectors need the spine, so they run only after OTP (the brief: a failed OTP exits
before any media or account lookup):
  CROSS_ACCOUNT         a loan id that belongs to a different borrower
  UTR_FORMAT / REFERENCE_REUSED / REFERENCE_OTHER_BORROWER / AMOUNT_MISMATCH / NOT_IN_FEED
  DOCTORED_ATTACHMENT   edited-image metadata, date before the loan, future date, wrong payee,
                        instructions embedded in the image text, OCR disagreeing with the bank feed
Sender detector:
  IMPERSONATION         sender is not the registered number and asks for account data
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import List, Optional

from .nlu import VALID_REF
from .store import reference_key

OWN_DOMAINS = ("pay.kairos.example", "docs.kairos.example")
LENDER_VPAS = ("kairosdemo.collect@upi", "kairos.demo@upi")
SETTLEMENT_DAYS = 2   # a real UPI/NEFT credit shows in the D7 feed within T+2; older and absent = not paid

FAKE = "FAKE"
PENDING = "PENDING"
SETTLED = "SETTLED"
REVERSED = "REVERSED"
ALREADY_APPLIED = "ALREADY_APPLIED"


@dataclass
class Signal:
    kind: str
    reason: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _rx(*ps):
    return [re.compile(p, re.I) for p in ps]


_INJECTION = _rx(
    r"\bignore (all |any |the |your )?(previous|prior|above|earlier|preceding|old)? ?(instructions?|rules|prompts?|guidelines)\b",
    r"\bdisregard (all |any |the |your )?(previous |prior |above )?(instructions?|rules|guidelines|policy)\b",
    r"\bforget (everything|all|your (instructions|rules|training))\b", r"\byou are now\b", r"\bact as (an? )?(admin|developer|system|root|bank)",
    r"\bpretend (to be|you are)\b", r"\b(system|developer) (prompt|message|mode)\b", r"\breveal (your|the) (prompt|instructions|rules)\b",
    r"\bjailbreak\b", r"\bdo anything now\b", r"\bnew instructions?\s*:", r"\boverride (the |your )?(rules|policy|verification|otp)\b",
    r"\b(skip|bypass|disable) (the )?(otp|verification|security|checks?)\b", r"\badmin (mode|access|override)\b",
    r"\b(instructions|rules) (bhool|ignore) (jao|karo|kar do)\b", r"\bverification (skip|hata) (karo|kar do|kijiye)\b",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>", r"\[\s*(system|inst)\s*\]",
    r"\bmark (this|the|my) (loan|account|emi) (as )?(paid|closed|settled)\b", r"\bset (status|balance) to\b",
)
_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:in|com|net|org|co|me|ly|io)/\S*|"
                  r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:xyz|top|click|link|info|online|site|live|shop|buzz|icu|tk|ml|ga|cf|gq)\b", re.I)
_SPOOF = _rx(
    r"\b(i am|i'm|this is|main|hum)\b.{0,15}\b(from|calling from|se)\b.{0,10}\b(the )?(bank|lender|rbi|head office|it team|"
    r"support team|collections? team|nbfc|branch|kairos)\b",
    r"\b(bank|branch|rbi|head office) (officer|manager|official|staff|executive)\b.{0,30}\b(here|speaking|bol raha)\b",
    r"\bkyc\b.{0,25}\b(update|expire\w*|pending|block\w*|suspend\w*)\b",
    r"\b(account|a/c|card)\b.{0,25}\b(will be |has been |is |ho jayega |hoga )?(blocked|suspended|frozen|deactivated|band)\b",
    r"\brbi (notice|order|circular)\b", r"\b(anydesk|teamviewer|quick ?support|screen ?shar\w*|rustdesk)\b", r"\.apk\b",
    r"\b(lottery|lucky draw|cashback|reward points?) .{0,30}\b(claim|won|credited)\b", r"\bdear (customer|user)\b.{0,80}\b(click|link|verify)\b",
)
_OTP_ASK = _rx(
    r"\b(tell|share|forward|read out|reveal|show|send me|give me|bata|batao|bata do|bhej do|de do|dedo|bolo)\b.{0,25}\b(otp|one[- ]time (password|code)|verification code|pin|cvv|password|mpin)\b",
    r"\b(otp|code|pin|cvv|password)\b.{0,25}\b(bata|batao|bata do|share kar|share karo|forward kar|bhej do|de do|dedo|bolo|bata dijiye)\b",
    r"\b(otp|code)\b.{0,30}\b(to|on|pe|par)\b.{0,15}\b(this|my new|new|another|other|alternate|different|dusre|naye|is)\s*(number|no\.?|phone|mobile|whatsapp)\b",
    r"\b(what|which|kya|kaunsa)\b.{0,15}\b(otp|code)\b.{0,25}\b(you|aapne|tumne|system)\b.{0,10}\b(sent|send|bheja|generated|generate)\b",
    r"\b(change|update|badal\w*)\b.{0,20}\b(registered|my)\b.{0,10}\b(number|mobile|phone)\b.{0,40}\b(otp|send|bhej)\b",
)
_OTHER_PEOPLE = _rx(
    r"\b(brother|sister|father|mother|husband|wife|friend|colleague|son|daughter|cousin|uncle|neighbou?r|bhai|behen|papa|"
    r"mummy|pati|patni|dost|beta|beti)('s|s)?\b.{0,20}\b(loan|account|emi|balance|details?|statement|outstanding|bakaya)\b",
    r"\b(all|every|other|sabhi|baaki|dusre)\b.{0,10}\b(customers?|borrowers?|accounts?|users?)\b.{0,30}\b(data|details?|list|balance|numbers?|phone)\b",
    r"\b(customer|borrower|defaulter)s? (list|data|database|dump)\b",
    r"\bsomeone else'?s\b.{0,20}\b(loan|account)\b",
)
ACCOUNT_DATA_ASK = re.compile(
    r"\b(balance|outstanding|due|emi|statement|loan (details?|info)|account (details?|info)|noc|kitna|bakaya|baki|"
    r"payment link|link|how much|details?|amount)\b", re.I)
_EDIT_SOFTWARE = ("photoshop", "gimp", "picsart", "snapseed", "canva", "pixlr", "photo editor", "lightroom", "fotor", "remini")


def scan_text(text: Optional[str]) -> List[Signal]:
    """Detectors that need only the message itself. Safe to run before verification."""
    t = " ".join((text or "").split())
    if not t:
        return []
    out: List[Signal] = []
    hit = next((rx.pattern for rx in _INJECTION if rx.search(t)), None)
    if hit:
        out.append(Signal("PROMPT_INJECTION", "message tries to change the assistant's instructions", {"rule": hit[:60]}))
    urls = [u for u in _URL.findall(t) if not any(d in u.lower() for d in OWN_DOMAINS)]
    if urls:
        out.append(Signal("PHISHING_LINK", "message carries a link that is not one of ours", {"links": len(urls)}))
    hit = next((rx.pattern for rx in _SPOOF if rx.search(t)), None)
    if hit:
        out.append(Signal("SPOOFED_LENDER", "message claims to come from the lender / bank / regulator", {"rule": hit[:60]}))
    hit = next((rx.pattern for rx in _OTP_ASK if rx.search(t)), None)
    if hit:
        out.append(Signal("OTP_SOCIAL_ENGINEERING", "message asks for, or to redirect, a one-time code", {"rule": hit[:60]}))
    hit = next((rx.pattern for rx in _OTHER_PEOPLE if rx.search(t)), None)
    if hit:
        out.append(Signal("OTHER_BORROWER_DATA", "message asks for another person's account data", {"rule": hit[:60]}))
    return out


def impersonation(sender_registered: bool, text: str, loan_ids_named: List[str]) -> Optional[Signal]:
    """Sender is not the registered number of any account, names a loan id and asks for its data."""
    if sender_registered or not loan_ids_named:
        return None
    if ACCOUNT_DATA_ASK.search(text or ""):
        return Signal("IMPERSONATION", "unregistered sender asks for account data of a named loan", {"loan_ids": len(loan_ids_named)})
    return None


def check_attachment(att: Optional[dict], now: datetime, loan_start: Optional[date]) -> List[Signal]:
    """Doctored-payment heuristics on (synthetic) attachment metadata + OCR fields."""
    if not att:
        return []
    out: List[Signal] = []
    meta = att.get("meta") or {}
    ocr = att.get("ocr") or {}
    sw = str(meta.get("software") or "").lower()
    if any(s in sw for s in _EDIT_SOFTWARE):
        out.append(Signal("DOCTORED_ATTACHMENT", "image was saved by an editing app", {"software": sw[:30]}))
    if meta.get("edited") is True:
        out.append(Signal("DOCTORED_ATTACHMENT", "image metadata marks it edited", {}))
    try:
        c, m = meta.get("created_at"), meta.get("modified_at")
        if c and m and (datetime.fromisoformat(m) - datetime.fromisoformat(c)).total_seconds() > 60:
            out.append(Signal("DOCTORED_ATTACHMENT", "image modified after capture", {"delta_s": int(
                (datetime.fromisoformat(m) - datetime.fromisoformat(c)).total_seconds())}))
    except ValueError:
        out.append(Signal("DOCTORED_ATTACHMENT", "unreadable image timestamps", {}))
    od = ocr.get("date")
    if od:
        try:
            d = date.fromisoformat(str(od)[:10])
            if d > now.date():
                out.append(Signal("DOCTORED_ATTACHMENT", "payment date on the image is in the future", {"date": d.isoformat()}))
            if loan_start and d < loan_start:
                out.append(Signal("DOCTORED_ATTACHMENT", "payment date on the image is before the loan started",
                                  {"date": d.isoformat()}))
        except ValueError:
            out.append(Signal("DOCTORED_ATTACHMENT", "unreadable payment date on the image", {}))
    payee = str(ocr.get("payee") or "").lower()
    if payee and payee not in LENDER_VPAS:
        out.append(Signal("DOCTORED_ATTACHMENT", "payee on the image is not the lender's collection account", {}))
    if ocr.get("text"):
        inj = [s for s in scan_text(str(ocr["text"])) if s.kind in ("PROMPT_INJECTION", "PHISHING_LINK")]
        if inj:
            out.append(Signal("DOCTORED_ATTACHMENT", "instructions embedded in the image text (ignored)", {"embedded": inj[0].kind}))
    return out


@dataclass
class RefCheck:
    verdict: str                       # FAKE | PENDING | SETTLED | REVERSED | ALREADY_APPLIED
    signal: Optional[Signal] = None
    feed: Optional[dict] = None
    payment: Optional[dict] = None


def check_reference(conn, reference: Optional[str], amount_paise: Optional[int], claimed_on: Optional[date],
                    borrower_id: str, today: date) -> RefCheck:
    """Judge a quoted bank reference against the spine and the D7 feed. Never marks anything paid."""
    if reference is None:
        return RefCheck(PENDING)
    key = reference_key(reference)
    norm = key.upper()
    if not VALID_REF.match(norm):
        return RefCheck(FAKE, Signal("UTR_FORMAT", "reference is not a valid UPI RRN (12 digits) or NEFT/RTGS UTR",
                                     {"length": len(norm)}))
    # Used already in the spine (verified payments). This borrower's own -> a repeated receipt request (E16).
    for p in conn.execute("SELECT p.payment_id, p.reference, p.amount_paise, p.obligation_id, p.reversed_on, l.borrower_id"
                          " FROM payments p JOIN loans l ON l.loan_id=p.loan_id").fetchall():
        if reference_key(p["reference"]) == key:
            if p["borrower_id"] != borrower_id:
                return RefCheck(FAKE, Signal("REFERENCE_REUSED", "reference already used for another account's payment", {}))
            return RefCheck(ALREADY_APPLIED, payment=dict(p))
    other = conn.execute("SELECT borrower_id FROM cm03_claims WHERE reference_key=? AND borrower_id<>? LIMIT 1",
                         (key, borrower_id)).fetchone()
    if other:
        return RefCheck(FAKE, Signal("REFERENCE_REUSED", "reference already claimed by another account", {}))
    feed = conn.execute("SELECT * FROM cm03_bank_feed WHERE reference_key=?", (key,)).fetchone()
    if feed is None:
        if claimed_on is not None and (today - claimed_on).days > SETTLEMENT_DAYS:
            return RefCheck(FAKE, Signal("NOT_IN_FEED", "no such credit in the bank feed although the claimed date is past T+2",
                                         {"days_since_claim": (today - claimed_on).days}))
        return RefCheck(PENDING)
    feed = dict(feed)
    if feed.get("borrower_id") and feed["borrower_id"] != borrower_id:
        return RefCheck(FAKE, Signal("REFERENCE_OTHER_BORROWER", "reference belongs to another account's credit", {}))
    if amount_paise is not None and amount_paise != feed["amount_paise"]:
        return RefCheck(FAKE, Signal("AMOUNT_MISMATCH", "claimed amount differs from the bank feed",
                                     {"claimed_paise": amount_paise, "feed_paise": feed["amount_paise"]}), feed)
    if feed["status"] == "REVERSED":
        return RefCheck(REVERSED, feed=feed)
    return RefCheck(SETTLED if feed["status"] == "SETTLED" else PENDING, feed=feed)
