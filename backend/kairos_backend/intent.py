"""K09 borrower-intent classification: deterministic rules first, one LLM call only when needed.

    IntentClassifier(llm=LunaClient()).classify(InboundMessage(...)) -> IntentResult

Order of decision (first that applies wins):
  1. Structured input  -> rules, never the LLM: DTMF digits, WhatsApp button / quick-reply payloads.
  2. STOP / UNSUBSCRIBE / opt-out keywords on any channel -> STOP, never the LLM.
  3. Free text: English + Hinglish keyword rules and date extraction ("25 ko", "kal", "parson",
     "25 tarikh", "2026-11-25", "25 Nov", weekdays). One clear intent -> rules. Any date found ->
     rules (conflicts resolved by PRECEDENCE and flagged ambiguous=True).
  4. No rule matched, or rules conflict without a date -> ONE call to the LLM (gpt-6-luna) on
     REDACTED, truncated text. Any failure -> IntentResult(OTHER, source="fallback", ambiguous=True).
The classifier never raises to the caller for LLM problems.

Intent names and how they map onto what the backend already handles
(exceptions.py / policy/K13-operational-exceptions, ledger.py, policy/K01 holds):

    intent        consumer                                             K13 event (exceptions.EVENTS)
    ------------  ---------------------------------------------------  -----------------------------
    NEEDS_TIME    K13 needs_time (rebuttals, no date)                  NEEDS_TIME
    PTP           K13 promise_to_pay -> K01 holds.PROMISE_TO_PAY       PTP_OFFERED (agreed_date set)
    CALLBACK      K13 callback (E08)                                   CALLBACK_REQUEST
    HARDSHIP      K13 hardship (E09) -> K01 holds.HARDSHIP             HARDSHIP (agreed_date optional)
    DISPUTE       K13 routed_exceptions.DISPUTE (ticket)               DISPUTE
    STOP          K01 holds.STOP_OPT_OUT -> Ledger.record_stop()       - (not a K13 event)
    CLAIMS_PAID   CM-03 payment verification; only a VERIFIED payment  - (Ledger.record_payment_verified
                  closes the obligation                                  after D7 verification)
    WRONG_PERSON  CM-03 verification gate (K13 not_handled: wrong      - (no disclosure before OTP)
                  number)
    OTHER         K01 stop_for_day BORROWER_RESPONSE only              -

Not classified here (no intent value yet): COMPLAINT, WAIVER_REQUEST, ABUSIVE, LEGAL_THREAT,
FRAUD_CLAIM. Such text usually lands in OTHER/DISPUTE and needs a human read; extend INTENTS and
the LLM enum together if Product wants them auto-routed.

LLM request (LunaClient) — shape verified against OpenAI docs on 2026-11-19:
  * https://developers.openai.com/api/docs/guides/text
      POST https://api.openai.com/v1/responses, headers Authorization: Bearer <key>,
      Content-Type: application/json; body fields model / instructions / input.
  * https://developers.openai.com/api/docs/models/gpt-6-luna(.md)
      model id "gpt-6-luna"; Responses endpoint supported; reasoning.effort supports
      none | low | medium (default) | high | xhigh | max; structured outputs supported.
  * https://developers.openai.com/api/docs/guides/reasoning
      reasoning: {"effort": ...}; max_output_tokens caps all generated tokens and a hit gives
      status "incomplete" + incomplete_details.reason "max_output_tokens"; store: false = stateless;
      usage.input_tokens / output_tokens / total_tokens / output_tokens_details.reasoning_tokens.
  * https://developers.openai.com/api/docs/guides/structured-outputs
      text: {"format": {"type": "json_schema", "name": ..., "schema": ..., "strict": true}};
      strict needs additionalProperties:false and every property in "required"; nullable via
      "type": ["string", "null"]; supported string keywords: pattern, format (maxLength is NOT
      listed as supported, so the 160-char limit is in the instruction and validated locally);
      refusals arrive as a content item of type "refusal".
  * https://community.openai.com/t/gpt-6-luna-occasionally-returns-an-additional-message-item-for-structured-outputs/1401886
      gpt-6-luna occasionally adds a second message item (phase "commentary") next to the JSON
      (phase "final_answer"); so we read message items ourselves, prefer phase "final_answer", and
      accept the first candidate that validates. No temperature/top_p is sent.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

IST = timezone(timedelta(hours=5, minutes=30), "IST")

NEEDS_TIME = "NEEDS_TIME"
PTP = "PTP"
CLAIMS_PAID = "CLAIMS_PAID"
CALLBACK = "CALLBACK"
HARDSHIP = "HARDSHIP"
STOP = "STOP"
DISPUTE = "DISPUTE"
WRONG_PERSON = "WRONG_PERSON"
OTHER = "OTHER"
INTENTS: Tuple[str, ...] = (NEEDS_TIME, PTP, CLAIMS_PAID, CALLBACK, HARDSHIP, STOP, DISPUTE, WRONG_PERSON, OTHER)

# Intent -> K13 event name understood by exceptions.handle(); None = handled elsewhere (see docstring).
K13_EVENT: Dict[str, Optional[str]] = {
    NEEDS_TIME: "NEEDS_TIME", PTP: "PTP_OFFERED", CALLBACK: "CALLBACK_REQUEST", HARDSHIP: "HARDSHIP",
    DISPUTE: "DISPUTE", STOP: None, CLAIMS_PAID: None, WRONG_PERSON: None, OTHER: None,
}

# When rules find several intents AND the message carries a date (structured -> no LLM), the
# safest reading wins: opt-out, then situations that pause/route, then commitments.
PRECEDENCE: Tuple[str, ...] = (STOP, WRONG_PERSON, HARDSHIP, DISPUTE, CLAIMS_PAID, CALLBACK, PTP, NEEDS_TIME)

SOURCES = ("rules", "model", "llm", "fallback")   # "model" only with use_stack=True (intent_stack tier 2)
SUMMARY_MAX = 160
LLM_MAX_INPUT_CHARS = 500


@dataclass(frozen=True)
class InboundMessage:
    borrower_id: str
    channel: str                         # sms | whatsapp | email | ivr | voicebot
    received_at: datetime                # naive = Asia/Kolkata local time
    text: Optional[str] = None
    dtmf: Optional[str] = None           # IVR keypad digits
    button_payload: Optional[str] = None # WhatsApp button / quick-reply payload
    language: Optional[str] = None


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    source: str                          # rules | llm | fallback
    summary: str                         # one CRM line (K05), <= 160 chars, no personal data
    ambiguous: bool
    agreed_date: Optional[str] = None    # YYYY-MM-DD (PTP date, hardship / callback date)
    llm_usage: Optional[dict] = None     # token usage when the LLM was called
    stack: Optional[dict] = None         # use_stack=True: the full intent_stack.StackResult (17 labels)

    @property
    def k13_event(self) -> Optional[str]:
        return K13_EVENT.get(self.intent)


# ---------------------------------------------------------------------------------------------
# Structured inputs
# ---------------------------------------------------------------------------------------------

# Default IVR menu (override per script via IntentClassifier(dtmf_map=...)).
DEFAULT_DTMF_MAP: Dict[str, str] = {
    "1": CLAIMS_PAID,    # "I have already paid"
    "2": NEEDS_TIME,     # "I need more time"
    "3": CALLBACK,       # "talk to an agent"
    "4": HARDSHIP,       # "facing a medical / financial emergency"
    "5": DISPUTE,        # "I dispute this amount"
    "8": WRONG_PERSON,   # "wrong number"
    "9": STOP,           # "stop calling me"
}

# WhatsApp quick-reply payloads (upper-cased; "PTP:2026-11-25" / "PTP_2026-11-25" carry a date).
BUTTON_MAP: Dict[str, str] = {
    "PAID": CLAIMS_PAID, "ALREADY_PAID": CLAIMS_PAID, "CLAIMS_PAID": CLAIMS_PAID,
    "NEED_TIME": NEEDS_TIME, "NEEDS_TIME": NEEDS_TIME, "PAY_LATER": NEEDS_TIME,
    "CALLBACK": CALLBACK, "CALL_ME": CALLBACK, "TALK_TO_AGENT": CALLBACK,
    "HARDSHIP": HARDSHIP, "DISPUTE": DISPUTE, "WRONG_PERSON": WRONG_PERSON, "WRONG_NUMBER": WRONG_PERSON,
    "STOP": STOP, "UNSUBSCRIBE": STOP, "OPT_OUT": STOP, "PTP": PTP, "PAY_TODAY": PTP, "PAY_TOMORROW": PTP,
}

# ---------------------------------------------------------------------------------------------
# Keyword rules (English + Hinglish). Patterns run on lower-cased, whitespace-collapsed text.
# ---------------------------------------------------------------------------------------------

_STOP_EXACT = re.compile(r"^\W*(stop|stop all|stopall|unsubscribe|unsub|opt ?out|cancel|end|quit)\W*$", re.I)
_STOP_ANY = re.compile(
    r"\bunsubscribe\b|\bopt[\s-]?out\b|\bstop\s+(all|messag\w*|msg\w*|sms|call\w*|send\w*|contact\w*|"
    r"bothering|it|this|now|please|pls)\b|^\s*stop\b|\bdo ?n[o']?t\s+(call|message|msg|text|contact)\b|"
    r"\b(msg|message|messages|sms|call|calls|phone)\s+(mat|na)\s+(karo|kare|karein|kijiye|bhejo|bhejiye|bhejna)\b|"
    r"\b(mat|na)\s+(bhejo|bhejiye|bhejna)\b|\b(msg|message|messages|sms|call|calls|messaging|calling)\s+band\s+"
    r"(karo|kare|karein|kijiye|kar do|kardo)\b|\bbhejna\s+band\b",
    re.I,
)
_STOP_UPPER = re.compile(r"\bSTOP\b")  # an upper-case STOP token anywhere is the opt-out keyword


def is_stop(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(_STOP_EXACT.search(text) or _STOP_UPPER.search(text) or _STOP_ANY.search(text))


_PROMISE = "PROMISE"  # internal: "will pay" — PTP with a date, NEEDS_TIME without one

_RULES: Dict[str, List[str]] = {
    WRONG_PERSON: [
        r"\bgalat (number|no\.?|numb[ae]r|insaan|aadmi|bande|person|vyakti)\b",
        r"\bwrong (number|no\.?|person|guy)\b", r"\bnot my (number|loan account)\b",
        r"\b(ye|yeh|is) (mera|meri) (number|no\.?) nahi\b", r"\bmera number nahi\b",
        r"\b(i|we) do ?n[o']?t know (him|her|this person|them)\b", r"\bno one by (that|this) name\b",
        r"\b(main|mai|me) (wo|woh|vo) nahi (hoon|hu|hun)\b",
    ],
    HARDSHIP: [
        r"\baccident\b", r"\bhospital\w*\b", r"\badmit(ted)?\b", r"\bicu\b", r"\bsurgery\b", r"\boperation\b",
        r"\bb[ae]+ma+r\b", r"\billness\b", r"\b(very |bahut )?sick\b", r"\bmedical\b", r"\bcancer\b",
        r"\b(job|naukri|nokri|kaam) (chali|chala|chhut|chut|gayi|gaya|gai|lost|gone)\b",
        r"\blost (my )?(job|work)\b", r"\blaid off\b", r"\blay ?off\b", r"\bunemployed\b", r"\bberozgar\b",
        r"\b(passed away|expired|death|died|guzar gay[ae]|maut|nidhan)\b",
        r"\bsalary (nahi|nhi) (aayi|ayi|mili|aa rahi)\b", r"\bsalary (ruk|band|stop)\w*\b",
        r"\b(flood|baadh|fire|aag lag)\w*\b", r"\bemergency\b",
    ],
    DISPUTE: [
        r"\bdispute\w*\b", r"\b(galat|wrong|incorrect|extra) (amount|charges?|emi|bill|penalty)\b",
        r"\b(amount|charges?|emi|penalty) (galat|wrong|incorrect)\b",
        r"\b(maine|mene|i) (ye |yeh |koi )?loan (nahi|nhi|never) (liya|li|took|taken)\b",
        r"\bnever (took|taken|applied for) (a |this |any )?loan\b", r"\bnot my loan\b",
        r"\b(loan|account|a/c|emi) (already |pehle hi |pehle )?(closed|close ho gaya|band ho gaya|foreclosed|settled)\b",
    ],
    CLAIMS_PAID: [
        r"\balready paid\b", r"\b(i|we|maine|mene|humne) (have |already )?paid\b", r"\bpaid (it|already|the|yesterday|today|on|last)\b",
        r"\bhas been paid\b", r"\bpayment (done|made|completed|successful|kar di(ya)?|ho gay[ai]|ho chuka|ho chuki|bhej di(ya)?)\b",
        r"\b(pay|bhar|jama|transfer|bhej|de|chuka|clear) (kar )?(diya|di|diye|chuka|chuki|chuke)\b",
        r"\b(kar|kr) (diya|di) (hai|h|tha)?\s*payment\b", r"\bemi (bhar|jama|de|pay kar) (di|diya|diye)\b",
    ],
    CALLBACK: [
        r"\bcall (me|back|karo|kariye|kare|karein|kar do|kardo|kijiye)\b", r"\bcallback\b", r"\bcall back\b",
        r"\b(phone|fone) (karo|kariye|kare|karein|kijiye|kar do)\b",
        r"\b(talk|speak) (to|with) (an? |your |some )?(agent|person|human|executive|manager|officer|someone)\b",
        r"\b(kisi|agent|aadmi|insaan|manager|executive|officer) se baat\b", r"\bbaat (karni|karna) (hai|h)\b",
        r"\bcall (kar|kr) (lena|lo|lijiye)\b",
    ],
    NEEDS_TIME: [
        r"\b(thoda|thodi|kuch|aur|zyada|jyada) (time|samay|waqt|din|days)\b", r"\btime (chahiye|chaiye|do|dijiye|de do|dedo)\b",
        r"\bneed (some |more |a little |a few |few |little )*(time|days)\b", r"\bgive me (some |more |a little )*(time|days)\b",
        r"\bsalary (ke baad|aane (pe|par|ke baad)|aayegi tab|milne (pe|par|ke baad))\b", r"\bafter (my )?salary\b",
        r"\bcan ?n?[o']?t pay (now|right now|today|this month|abhi)\b", r"\babhi (nahi|nhi|paise nahi|paisa nahi)\b",
        r"\b(paise|paisa|funds?|money) (nahi|nhi|not available)\b", r"\bnext (week|month)\b",
        r"\bagl[ea] (hafte|hafta|mahine|mahina|week|month)\b", r"\bbaad (me|mein|mai)\b", r"\bfew days\b",
        r"\bkuch din\b", r"\bextension\b", r"\b(aaj|kal|today|tomorrow|abhi|is mahine) (nahi|nhi|not|na)\b",
        r"\b(nahi|nhi) (ho )?(payega|paega|payegi|sakta|sakti|sakega|sakenge|hoga)\b",
    ],
    _PROMISE: [
        r"\b(kar|kr|de|bhar|jama|chuka|bhej|pay kar) (dunga|dungi|denge|doonga|doongi|dunga|dega|degi|dege)\b",
        r"\b(pay|payment|jama|bhugtan) (karunga|karungi|karenge|kar dunga|kar dungi)\b",
        r"\b(i|we) ?(will|'ll|shall) (pay|clear|transfer|deposit|make (the )?payment)\b", r"\bwill pay\b",
        r"\bpromise\b", r"\bpaying (on|by|tomorrow|today)\b",
    ],
}
_COMPILED: Dict[str, List[re.Pattern]] = {k: [re.compile(p, re.I) for p in v] for k, v in _RULES.items()}

# Short keyword per hardship match for the template summary (no personal data).
_HARDSHIP_KIND = [(re.compile(p, re.I), k) for p, k in [
    (r"accident", "accident"), (r"hospital|admit|icu|surgery|operation|b[ae]+ma+r|illness|sick|medical|cancer", "medical"),
    (r"job|naukri|nokri|kaam|laid|lay ?off|unemployed|berozgar|salary", "income loss"),
    (r"passed away|expired|death|died|guzar|maut|nidhan", "bereavement"),
]]


def rule_matches(text: str) -> List[str]:
    t = " ".join(text.lower().split())
    return [k for k, pats in _COMPILED.items() if any(p.search(t) for p in pats)]


# ---------------------------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_WEEKDAYS = {
    0: ["monday", "somvar", "somvaar", "somwar"], 1: ["tuesday", "mangalvar", "mangalwar", "mangalvaar"],
    2: ["wednesday", "budhvar", "budhwar", "budhvaar"], 3: ["thursday", "guruvar", "guruwar", "veervar", "brihaspativar"],
    4: ["friday", "shukravar", "shukrawar", "shukravaar"], 5: ["saturday", "shanivar", "shaniwar", "shanivaar"],
    6: ["sunday", "ravivar", "raviwar", "itvaar", "itwar"],
}
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(w for ws in _WEEKDAYS.values() for w in ws) + r")\b", re.I)

_ISO = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\b")
_DM = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_D_MON = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?" + _MON_RE + r"\b", re.I)
_MON_D = re.compile(r"\b" + _MON_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_D_HINGLISH = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:ko|tarikh|tareekh|tarik|taarikh|tak|date)\b", re.I)
_D_ORD = re.compile(r"\b(?:on|by|before)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b|\b(\d{1,2})(?:st|nd|rd|th)\b", re.I)
_REL = [
    (re.compile(r"\bday after tomorrow\b", re.I), 2),
    (re.compile(r"\b(parson|parso|parsoon|parsun)\b", re.I), 2),
    (re.compile(r"\b(tomorrow|tmrw|tmr|kal|kl)\b", re.I), 1),
    (re.compile(r"\b(today|aaj)\b", re.I), 0),
]


def _local_day(dt: datetime) -> date:
    return (dt.astimezone(IST) if dt.tzinfo else dt).date()


def _mk(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _day_of_month(today: date, d: int) -> Optional[date]:
    """Next occurrence of day-of-month d (this month if not yet passed, else next month)."""
    if not 1 <= d <= 31:
        return None
    y, m = today.year, today.month
    for _ in range(3):
        cand = _mk(y, m, d)
        if cand and cand >= today:
            return cand
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None


def _month_day(today: date, m: int, d: int) -> Optional[date]:
    cand = _mk(today.year, m, d)
    if cand and cand < today - timedelta(days=31):  # "5 Jan" said in December -> next year
        cand = _mk(today.year + 1, m, d)
    return cand


def extract_date(text: str, today: date) -> Tuple[Optional[date], bool]:
    """(date, explicit) from free text. explicit=True for calendar dates, False for relative words.
    Only the first date found counts. "abhi"/"aaj" resolve to today."""
    t = text.lower()
    m = _ISO.search(t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3))), True
    m = _DMY.search(t)
    if m:
        y = int(m.group(3))
        return _mk(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))), True
    m = _D_MON.search(t)
    if m:
        return _month_day(today, _MONTHS[m.group(2)[:3]], int(m.group(1))), True
    m = _MON_D.search(t)
    if m:
        return _month_day(today, _MONTHS[m.group(1)[:3]], int(m.group(2))), True
    m = _DM.search(t)
    if m and 1 <= int(m.group(2)) <= 12:
        return _month_day(today, int(m.group(2)), int(m.group(1))), True
    m = _D_HINGLISH.search(t)
    if m:
        return _day_of_month(today, int(m.group(1))), True
    m = _D_ORD.search(t)
    if m:
        return _day_of_month(today, int(m.group(1) or m.group(2))), True
    for rx, offset in _REL:
        if rx.search(t):
            return today + timedelta(days=offset), False
    m = _WEEKDAY_RE.search(t)
    if m:
        word = m.group(1).lower()
        wd = next(k for k, ws in _WEEKDAYS.items() if word in ws)
        ahead = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead), False
    return None, False


# ---------------------------------------------------------------------------------------------
# Redaction (everything that leaves the process goes through this)
# ---------------------------------------------------------------------------------------------

_RX_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)
_RX_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")
_RX_UPI = re.compile(r"\b[\w.-]{2,}@[a-z][a-z0-9]{1,}\b", re.I)
_RX_REF = re.compile(
    r"\b(utr|rrn|upi\s*ref(erence)?|ref(erence)?|txn|transaction(\s*id)?|order\s*id|receipt)"
    r"(\s*(no\.?|number|num|id|#))?\s*[:#-]?\s*(?=[a-z]*\d)[a-z0-9]{6,}\b", re.I)
_RX_LOAN = re.compile(r"\b(pl|ln|lan|loan|ac|acc|acct|a/c|account|cust|customer)(\s*(no\.?|number|num|id|#))?"
                      r"\s*[:#-]?\s*[a-z]{0,4}\d{4,}\b", re.I)
_RX_ALNUM_ID = re.compile(r"\b[a-z]{1,6}\d{5,}[a-z0-9]*\b", re.I)
_RX_PHONE = re.compile(r"(?<!\w)(\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\w)")
_RX_AMOUNT = re.compile(
    r"(₹|\brs\.?|\binr\b|\brupees?\b)\s*\d[\d,]*(\.\d+)?(\s*(k|lakh|lac|lakhs|thousand|hazaar|hazar)\b)?"
    r"|\b\d[\d,]*(\.\d+)?\s*(/-|₹|rs\b\.?|inr\b|rupees?\b|rupaye\b|rupay\b|k\b|lakh\w*|lac\b|thousand\b|hazaa?r\b)",
    re.I)
_RX_BIGNUM = re.compile(r"\b\d[\d,]{3,}\b")
_RX_NAME_INTRO = re.compile(
    r"(?i:\b(my name is|this is|i am|i'm|mera naam|mera nam|naam))\s+([A-Z][a-z]+(\s+[A-Z][a-z]+)?)")
_DATE_PROTECT = re.compile(r"\b20\d\d-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-](\d{4}|\d{2})\b")


def redact(text: str, max_chars: int = LLM_MAX_INPUT_CHARS, known: Iterable[str] = ()) -> str:
    """Replace personal / financial identifiers with placeholders and truncate to max_chars.
    Calendar dates are kept (the model needs them); everything numeric with 4+ digits goes."""
    s = " ".join((text or "").split())
    for k in known:
        if k and len(k) >= 2:
            s = re.sub(re.escape(k), "[ID]", s, flags=re.I)
    kept: List[str] = []

    def _keep(m: re.Match) -> str:
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    s = _DATE_PROTECT.sub(_keep, s)
    s = _RX_URL.sub("[URL]", s)
    s = _RX_EMAIL.sub("[EMAIL]", s)
    s = _RX_UPI.sub("[UPI_ID]", s)
    s = _RX_REF.sub("[REF]", s)
    s = _RX_LOAN.sub("[ACCOUNT]", s)
    s = _RX_PHONE.sub("[PHONE]", s)
    s = _RX_AMOUNT.sub("[AMOUNT]", s)
    s = _RX_ALNUM_ID.sub("[ID]", s)
    s = _RX_BIGNUM.sub("[NUMBER]", s)
    s = _RX_NAME_INTRO.sub(lambda m: m.group(1) + " [NAME]", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], s)
    return s[:max_chars]


# ---------------------------------------------------------------------------------------------
# LLM client (OpenAI Responses API, stdlib only)
# ---------------------------------------------------------------------------------------------

Transport = Callable[[str, Dict[str, str], bytes, float], Tuple[int, bytes]]

LLM_INTENT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS),
                   "description": "Borrower intent. PTP only when a specific payment date is given."},
        "summary": {"type": "string",
                    "description": "One neutral CRM line, at most 160 characters, no names or numbers."},
        "ambiguous": {"type": "boolean", "description": "true if the message could reasonably mean something else"},
        "agreed_date": {"type": ["string", "null"], "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                        "description": "YYYY-MM-DD for a promised payment / callback / hardship date, else null"},
    },
    "required": ["intent", "summary", "ambiguous", "agreed_date"],
    "additionalProperties": False,
}

LLM_INSTRUCTIONS = (
    "You classify one reply from a borrower to a loan-repayment reminder in India (English, Hindi or "
    "Hinglish). Return only the JSON object. Intents: NEEDS_TIME (wants time, no date), PTP (promises "
    "to pay on a specific date), CLAIMS_PAID (says already paid), CALLBACK (wants a person to call), "
    "HARDSHIP (illness, accident, job loss, death, disaster), STOP (opt out of messages), DISPUTE "
    "(contests loan or amount), WRONG_PERSON (wrong number / not the borrower), OTHER. Resolve relative "
    "dates from the given today date. summary: one neutral line, max 160 characters, no names, numbers "
    "or placeholders. Set ambiguous=true when unsure."
)


def default_transport(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https URL)
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


def validate_llm_output(obj: Any) -> Optional[str]:
    """None when obj matches LLM_INTENT_SCHEMA (plus the 160-char and real-date checks), else why not."""
    if not isinstance(obj, dict):
        return "not an object"
    if set(obj) != set(LLM_INTENT_SCHEMA["required"]):
        return f"keys {sorted(obj)}"
    if obj["intent"] not in INTENTS:
        return f"intent {obj['intent']!r} not in enum"
    s = obj["summary"]
    if not isinstance(s, str) or not s.strip() or len(s) > SUMMARY_MAX:
        return "summary missing or longer than 160 chars"
    if not isinstance(obj["ambiguous"], bool):
        return "ambiguous not boolean"
    d = obj["agreed_date"]
    if d is not None:
        if not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            return "agreed_date not YYYY-MM-DD"
        try:
            date.fromisoformat(d)
        except ValueError:
            return "agreed_date not a real date"
    return None


@dataclass
class LlmReply:
    data: Optional[dict]                 # validated JSON, or None
    usage: Optional[dict]
    error: Optional[str] = None


class LunaClient:
    """One Responses API call to gpt-6-luna: reasoning effort none, no tools, strict JSON schema,
    store false. Never raises: problems come back as LlmReply(data=None, error=...)."""

    URL = "https://api.openai.com/v1/responses"
    MODEL = "gpt-6-luna"

    def __init__(self, api_key: Optional[str] = None, transport: Optional[Transport] = None,
                 timeout: float = 8.0, max_output_tokens: int = 256, url: str = URL,
                 env: Optional[Dict[str, str]] = None):
        self._api_key = api_key
        self.transport = transport or default_transport
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.url = url
        self._env = env

    def api_key(self) -> Optional[str]:
        env = os.environ if self._env is None else self._env
        return self._api_key or env.get("OPENAI_API_KEY") or None

    def build_request(self, redacted_text: str, today: date, channel: str) -> Dict[str, Any]:
        user = (f"Today (Asia/Kolkata): {today.isoformat()}\nChannel: {channel}\n"
                f"Borrower message (redacted): {redacted_text[:LLM_MAX_INPUT_CHARS]}")
        return {
            "model": self.MODEL,
            "instructions": LLM_INSTRUCTIONS,
            "input": user,
            "reasoning": {"effort": "none"},
            "text": {"format": {"type": "json_schema", "name": "borrower_intent",
                                "schema": LLM_INTENT_SCHEMA, "strict": True}},
            "store": False,
            "max_output_tokens": self.max_output_tokens,
        }

    def classify(self, redacted_text: str, today: date, channel: str) -> LlmReply:
        key = self.api_key()
        if not key:
            return LlmReply(None, None, "OPENAI_API_KEY missing")
        body = json.dumps(self.build_request(redacted_text, today, channel)).encode("utf-8")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        t0 = time.monotonic()
        try:
            status, raw = self.transport(self.url, headers, body, self.timeout)
        except Exception as e:  # timeout, DNS, connection reset ...
            return LlmReply(None, None, f"transport error: {type(e).__name__}")
        latency_ms = int((time.monotonic() - t0) * 1000)
        if status != 200:
            return LlmReply(None, None, f"HTTP {status}")
        try:
            resp = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return LlmReply(None, None, "response not JSON")
        if not isinstance(resp, dict):
            return LlmReply(None, None, "response not an object")
        usage = self._usage(resp, latency_ms)
        if resp.get("status") not in (None, "completed"):
            reason = (resp.get("incomplete_details") or {}).get("reason") if isinstance(resp.get("incomplete_details"), dict) else None
            return LlmReply(None, usage, f"status {resp.get('status')}" + (f" ({reason})" if reason else ""))
        last_err = "no output text"
        for text in self._candidate_texts(resp):
            try:
                obj = json.loads(text)
            except ValueError:
                last_err = "output not JSON"
                continue
            err = validate_llm_output(obj)
            if err is None:
                return LlmReply(obj, usage)
            last_err = f"schema: {err}"
        return LlmReply(None, usage, last_err)

    @staticmethod
    def _candidate_texts(resp: dict) -> List[str]:
        """output_text parts of message items; phase "final_answer" first; refusals are skipped."""
        final: List[str] = []
        other: List[str] = []
        for item in resp.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            bucket = final if item.get("phase") == "final_answer" else other
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    bucket.append(part["text"])
        texts = final + other
        if isinstance(resp.get("output_text"), str):  # present in SDK objects, not always in raw REST
            texts.append(resp["output_text"])
        return texts

    def _usage(self, resp: dict, latency_ms: int) -> Optional[dict]:
        u = resp.get("usage")
        if not isinstance(u, dict):
            return {"model": resp.get("model", self.MODEL), "latency_ms": latency_ms}
        out = {"model": resp.get("model", self.MODEL), "latency_ms": latency_ms,
               "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
               "total_tokens": u.get("total_tokens")}
        rt = (u.get("output_tokens_details") or {}).get("reasoning_tokens") if isinstance(u.get("output_tokens_details"), dict) else None
        if rt is not None:
            out["reasoning_tokens"] = rt
        return out


# ---------------------------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------------------------

def _summary(intent: str, channel: str, agreed: Optional[date], detail: str = "", ambiguous: bool = False) -> str:
    d = agreed.isoformat() if agreed else None
    base = {
        NEEDS_TIME: "Borrower asked for more time; no payment date given.",
        PTP: f"Borrower promised to pay on {d}." if d else "Borrower promised to pay; no date given.",
        CLAIMS_PAID: "Borrower says payment already made; verify before further outreach.",
        CALLBACK: f"Borrower asked for a call back from a person{' on ' + d if d else ''}.",
        HARDSHIP: f"Borrower reported hardship{' (' + detail + ')' if detail else ''}"
                  f"{'; date given ' + d if d else ''}.",
        STOP: "Borrower opted out (STOP); suppress all channels.",
        DISPUTE: "Borrower disputes the loan or amount; ticket for supervisor.",
        WRONG_PERSON: "Recipient says wrong number or person; no disclosure, verify contact.",
        OTHER: "Borrower reply not classified; needs review.",
    }[intent]
    s = f"[{channel}] {base}" + (" Ambiguous reply." if ambiguous else "")
    return s[:SUMMARY_MAX]


class IntentClassifier:
    """Rules first; at most one LLM call per message, only for unmatched or conflicting free text.

    use_stack=True (or env KAIROS_INTENT_STACK=1 when use_stack is None) sends free text through the
    tiered intent_stack (rules -> small model -> local LLM -> this classifier's `llm` as the cloud
    tier -> abstain) and maps its 17 labels back onto INTENTS; DTMF, buttons and STOP keywords are
    handled here exactly as before. The full stack result is on IntentResult.stack. Off by default
    so CM-03 opts in explicitly."""

    def __init__(self, llm: Optional[LunaClient] = None, dtmf_map: Optional[Dict[str, str]] = None,
                 use_stack: Optional[bool] = None, stack: Any = None):
        self.llm = llm
        self.dtmf_map = dict(DEFAULT_DTMF_MAP if dtmf_map is None else dtmf_map)
        if use_stack is None:
            use_stack = os.environ.get("KAIROS_INTENT_STACK", "").strip().lower() in ("1", "true", "on", "yes")
        self.use_stack = bool(use_stack)
        self._stack = stack

    # -- public -------------------------------------------------------------------------------
    def classify(self, msg: InboundMessage, known_names: Sequence[str] = ()) -> IntentResult:
        today = _local_day(msg.received_at)
        if msg.dtmf is not None and str(msg.dtmf).strip():
            return self._dtmf(msg, today)
        if msg.button_payload is not None and str(msg.button_payload).strip():
            return self._button(msg, today)
        text = (msg.text or "").strip()
        if not text:
            return self._rules(OTHER, msg, 0.5, ambiguous=True)
        if is_stop(text):
            return self._rules(STOP, msg, 1.0)
        if self.use_stack:
            return self._from_stack(msg, text, today, known_names)
        return self._free_text(msg, text, today, known_names)

    # -- tiered stack (opt-in) ----------------------------------------------------------------
    def stack(self):
        if self._stack is None:
            from .intent_stack import IntentStack, OllamaClient  # lazy: intent_stack imports this module
            local = OllamaClient() if os.environ.get("KAIROS_LOCAL_LLM_MODEL") else None
            from .sarvam.prism import shared_prism
            self._stack = IntentStack(local_llm=local, cloud_llm=self.llm, prism=shared_prism())
        return self._stack

    def _from_stack(self, msg: InboundMessage, text: str, today: date, known: Sequence[str]) -> IntentResult:
        from .intent_stack import TO_LEGACY, hardship_kind
        r = self.stack().classify(text, today, msg.channel, [msg.borrower_id, *known])
        intent = TO_LEGACY[r.label]
        agreed_s = r.entities.get("date") if intent in (PTP, HARDSHIP, CALLBACK) else None
        agreed = date.fromisoformat(agreed_s) if agreed_s else None
        if intent == PTP and agreed is None:
            intent = NEEDS_TIME
        ambiguous = r.needs_human and r.label != HARDSHIP or r.confidence < 0.7
        source = {"rules": "rules", "small_model": "model", "prism": "llm", "local_llm": "llm",
                  "cloud_llm": "llm"}.get(r.tier_used, "fallback")
        cloud = r.evidence.get("cloud_llm") or {}
        usage = cloud.get("usage")
        if source == "fallback":
            summary = f"[{msg.channel}] Borrower reply not classified (all tiers unsure); needs review."[:SUMMARY_MAX]
            return IntentResult(OTHER, 0.0, "fallback", summary, True, None, usage, r.to_dict())
        if r.tier_used == "cloud_llm" and (cloud.get("output") or {}).get("summary"):
            summary = " ".join(cloud["output"]["summary"].split())[:SUMMARY_MAX]
        else:
            detail = hardship_kind(text) if intent == HARDSHIP else ""
            summary = _summary(intent, msg.channel, agreed, detail, ambiguous)
        return IntentResult(intent, r.confidence, source, summary, ambiguous, agreed.isoformat() if agreed else None,
                            usage, r.to_dict())

    # -- structured ---------------------------------------------------------------------------
    def _rules(self, intent: str, msg: InboundMessage, conf: float, ambiguous: bool = False,
               agreed: Optional[date] = None, detail: str = "") -> IntentResult:
        if intent not in (PTP, HARDSHIP, CALLBACK):
            agreed = None
        if intent == PTP and agreed is None:
            intent = NEEDS_TIME  # a promise without a date is "needs time" (K13 needs_time trigger)
        return IntentResult(intent, conf, "rules", _summary(intent, msg.channel, agreed, detail, ambiguous),
                            ambiguous, agreed.isoformat() if agreed else None, None)

    def _dtmf(self, msg: InboundMessage, today: date) -> IntentResult:
        digits = re.sub(r"\s", "", str(msg.dtmf))
        intent = self.dtmf_map.get(digits) or self.dtmf_map.get(digits[:1])
        if intent is None:
            return self._rules(OTHER, msg, 0.5, ambiguous=True)
        agreed = today + timedelta(days=1) if intent == PTP else None
        return self._rules(intent, msg, 0.99, agreed=agreed)

    def _button(self, msg: InboundMessage, today: date) -> IntentResult:
        raw = str(msg.button_payload).strip()
        up = raw.upper()
        m = re.match(r"^PTP[:_ -]?(\d{4}-\d{2}-\d{2})$", up)
        if m:
            d = None
            try:
                d = date.fromisoformat(m.group(1))
            except ValueError:
                pass
            return self._rules(PTP, msg, 0.99, ambiguous=d is None, agreed=d)
        key = re.sub(r"[\s-]+", "_", up)
        intent = BUTTON_MAP.get(key)
        if intent == PTP:
            agreed = today + timedelta(days=1) if key == "PAY_TOMORROW" else today if key == "PAY_TODAY" else None
            return self._rules(PTP, msg, 0.99, agreed=agreed)
        if intent:
            return self._rules(intent, msg, 0.99)
        if is_stop(raw):
            return self._rules(STOP, msg, 1.0)
        return self._rules(OTHER, msg, 0.5, ambiguous=True)  # unknown payload: never guessed by the LLM

    # -- free text ----------------------------------------------------------------------------
    def _free_text(self, msg: InboundMessage, text: str, today: date, known: Sequence[str]) -> IntentResult:
        found = set(rule_matches(text))
        agreed, explicit = extract_date(text, today)
        if CLAIMS_PAID in found:
            found.discard(_PROMISE)           # "de diya" also looks like a promise stem
        if _PROMISE in found:
            found.discard(_PROMISE)
            found.add(PTP if agreed else NEEDS_TIME)
        if agreed and NEEDS_TIME in found and (explicit or PTP in found) and not (found - {NEEDS_TIME, PTP}):
            found = {PTP}                     # "time chahiye, 25 ko dunga": a date ends the rebuttals
        elif NEEDS_TIME in found and PTP not in found and not explicit:
            agreed = None                     # "aaj nahi ho payega": a relative word is not a promise date
        if HARDSHIP in found:
            found -= {NEEDS_TIME, PTP}        # hardship subsumes "need time" (hold, no ask)
        detail = ""
        if HARDSHIP in found:
            detail = next((k for rx, k in _HARDSHIP_KIND if rx.search(text)), "")

        if len(found) == 1:
            intent = found.pop()
            return self._rules(intent, msg, 0.9, agreed=agreed, detail=detail)
        if not found and agreed is not None:
            return self._rules(PTP, msg, 0.75, agreed=agreed)   # bare date reply to a date ask
        if found and agreed is not None:
            intent = next(i for i in PRECEDENCE if i in found)  # dates are structured: no LLM
            return self._rules(intent, msg, 0.6, ambiguous=True, agreed=agreed, detail=detail)
        return self._ask_llm(msg, text, today, known)

    def _ask_llm(self, msg: InboundMessage, text: str, today: date, known: Sequence[str]) -> IntentResult:
        if self.llm is None:
            return self._fallback(msg, "no LLM configured", None)
        try:
            safe = redact(text, LLM_MAX_INPUT_CHARS, known=[msg.borrower_id, *known])
            reply = self.llm.classify(safe, today, msg.channel)
        except Exception as e:  # defensive: an LLM problem never reaches the caller
            return self._fallback(msg, f"llm error {type(e).__name__}", None)
        if reply.data is None:
            return self._fallback(msg, reply.error or "invalid output", reply.usage)
        d = reply.data
        intent, agreed = d["intent"], d["agreed_date"]
        ambiguous = d["ambiguous"]
        if intent == PTP and not agreed:
            intent, ambiguous = NEEDS_TIME, True
        if intent not in (PTP, HARDSHIP, CALLBACK):
            agreed = None
        return IntentResult(intent, 0.55 if ambiguous else 0.8, "llm", " ".join(d["summary"].split())[:SUMMARY_MAX],
                            ambiguous, agreed, reply.usage)

    def _fallback(self, msg: InboundMessage, why: str, usage: Optional[dict]) -> IntentResult:
        summary = f"[{msg.channel}] Borrower reply not classified ({why}); needs review."[:SUMMARY_MAX]
        return IntentResult(OTHER, 0.0, "fallback", summary, True, None, usage)
