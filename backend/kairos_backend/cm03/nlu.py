"""CM-03 language understanding: servicing intents on top of K09's intent.py.

Deterministic rules first. K09's IntentClassifier (rules, then gpt-6-luna only for ambiguous free
text on REDACTED input, disabled when no key is set) supplies the collection intents: CLAIMS_PAID,
PTP, NEEDS_TIME, CALLBACK, HARDSHIP, DISPUTE, STOP, WRONG_PERSON. This module adds what a
servicing chat needs: balance / due date, payment link, statement, NOC, loan details, language
change, greetings / thanks, waiver / complaint, and the extractors (OTP code, bank reference,
amount, claimed payment date, loan ids).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence

from .. import intent as k09

# CM-03 intents (K09 names are reused for the collection intents)
BALANCE = "BALANCE"
PAY_LINK = "PAY_LINK"
STATEMENT = "STATEMENT"
NOC = "NOC"
LOAN_DETAILS = "LOAN_DETAILS"
LANG_CHANGE = "LANG_CHANGE"
GREETING = "GREETING"
THANKS = "THANKS"
WAIVER = "WAIVER_REQUEST"
COMPLAINT = "COMPLAINT"
MENU_PICK = "MENU_PICK"
RESEND_OTP = "RESEND_OTP"

# intent_stack labels (use_stack=True) that are CM-03 servicing asks rather than K09 collection intents
STACK_SERVICING = {"BALANCE_QUERY": BALANCE, "LINK_REQUEST": PAY_LINK, "STATEMENT_REQUEST": STATEMENT, "GREETING": GREETING}

MENU = {"1": BALANCE, "2": PAY_LINK, "3": k09.CLAIMS_PAID, "4": k09.NEEDS_TIME, "5": STATEMENT, "6": k09.CALLBACK}

_R = lambda *ps: [re.compile(p, re.I) for p in ps]  # noqa: E731

_SERVICING = {
    PAY_LINK: _R(r"\b(payment|pay|upi|bhugtan)\s*(link|url)\b", r"\blink\s*(bhej|send|share|chahiye|do|dijiye|dedo|please|pls)",
                 r"\b(send|share|give)\b.{0,15}\blink\b", r"\bhow (can|do|to) i pay\b", r"\b(kaise|kahan|kaha)\s*(pay|bhar|jama|payment)",
                 r"\bpay (online|now)\b", r"\blink (nahi|nhi|not) (chal|work|khul)", r"\b(broken|expired|invalid) link\b",
                 r"\bpay karna (hai|h|chahta|chahti)\b", r"\bpayment karna (hai|h)\b", r"\bwant to pay\b", r"\bi('d| would) like to pay\b"),
    BALANCE: _R(r"\bbalance\b", r"\boutstanding\b", r"\bkitna\b.{0,20}\b(baki|baaki|bakaya|due|dena|pending|hai)\b",
                r"\b(baki|baaki|bakaya)\b", r"\bdue (amount|date)\b", r"\bemi\b.{0,20}\b(kab|when|date|due|kitni|amount|how much)\b",
                r"\b(kab|when)\b.{0,20}\bemi\b", r"\bhow much\b.{0,20}\b(owe|due|pay|pending)\b", r"\bnext emi\b",
                r"\bamount due\b", r"\bdues?\b\s*(kitna|kya|batao|bata)", r"\bkitni emi\b", r"\bpending amount\b"),
    NOC: _R(r"\bnoc\b", r"\bno objection\b", r"\bclosure (letter|certificate)\b", r"\bloan closure\b"),
    STATEMENT: _R(r"\bstatement\b", r"\bsoa\b", r"\bpayment history\b", r"\breceipt\b", r"\brasid\b", r"\braseed\b"),
    LOAN_DETAILS: _R(r"\bloan (details?|info|information|summary)\b", r"\b(tenure|interest rate|roi)\b",
                     r"\bkitni (emi|kisht) (baki|bachi)\b", r"\bloan ki (details?|jankari|jaankari)\b"),
    WAIVER: _R(r"\bwaive\w*\b", r"\bwaiver\b", r"\b(penalty|late fee|charges?)\b.{0,20}\b(maaf|hata|remove|reverse|cancel)\w*",
               r"\bmaaf (kar|kr)"),
    "DISPUTE": _R(r"\b(amount|emi|charges?|bill|penalty|interest|balance)\b.{0,12}\b(is|are|hai|h)?\s*(wrong|incorrect|galat|not correct|sahi nahi)\b",
                  r"\b(wrong|incorrect|galat|extra)\b.{0,8}\b(amount|charges?|emi|bill|penalty|interest)\b", r"\bdispute\w*\b",
                  r"\boverchar\w*\b", r"\bcharged (me )?(twice|extra|double)\b"),
    COMPLAINT: _R(r"\bcomplain\w*\b", r"\bshikayat\b", r"\bharass\w*\b", r"\bombudsman\b"),
}
_ORDER = (PAY_LINK, NOC, STATEMENT, LOAN_DETAILS, BALANCE, WAIVER, COMPLAINT)

_GREETING = re.compile(r"^\W*(hi+|hello+|hey+|hii+|namaste|namaskar|namaskaar|good (morning|afternoon|evening)|"
                       r"नमस्ते|हेलो|hlo|helo|start)(\s+(ji|sir|madam|mam|maam|there|team|kaira))?\W*$", re.I)
_THANKS = re.compile(r"^\W*(thanks?( you)?|thank u|thx|ty|ok(ay)?( thanks?)?|dhanyavad|dhanyawad|shukriya|"
                     r"धन्यवाद|no( thanks?)?|nahi|nothing( else)?|bas|done|that'?s all|theek hai|thik hai)\W*$", re.I)
_RESEND = re.compile(r"\b(resend|send again|new (otp|code)|otp (nahi|nhi|not) (aaya|aya|mila|received|came)|"
                     r"didn'?t (get|receive) (the )?(otp|code)|dobara bhej|phir se bhej)\b", re.I)
_LANG_HI = re.compile(r"\b(hindi|हिंदी|हिन्दी)\b", re.I)
_LANG_EN = re.compile(r"\b(english|angrezi|अंग्रेज़ी|अंग्रेजी)\b", re.I)
_LANG_SWITCH_CUE = re.compile(r"\b(in|me|mein|mai|please|pls|plz|baat|reply|talk|speak|chahiye|karo|kijiye|language|bhasha)\b|"
                              r"^\W*\w+\W*$|में", re.I)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HINGLISH = re.compile(r"\b(kya|hai|hain|mera|meri|mujhe|kar|kr|nahi|nhi|haan|ji|kab|kitna|kitni|paisa|paise|aap|"
                       r"karo|kijiye|bhej|bhejo|diya|di|tha|hoga|chahiye|baat|bata|batao|aur|abhi|kal|parso|thoda)\b", re.I)

_BROKEN_LINK = re.compile(r"\blink (nahi|nhi|not) (chal|work|khul)|\b(broken|expired|invalid) link\b", re.I)

RX_OTP = re.compile(r"(?<![\d.])(\d{6})(?![\d.])")
RX_OTP_SPACED = re.compile(r"(?<!\d)(\d(?:[\s-]?\d){5})(?!\d)")
# bank references: after a cue word, or a bare UPI RRN (12 digits) / NEFT-RTGS UTR (4 letters + 12-18)
RX_REF_CUE = re.compile(r"\b(?:utr|rrn|ref(?:erence)?|txn|transaction(?:\s*id)?|upi\s*ref)\s*(?:no\.?|number|id|#)?\s*[:#-]?\s*"
                        r"(?=\S*[0-9\uFF10-\uFF19])([A-Za-z0-9\uFF10-\uFF19\uFF21-\uFF3A\uFF41-\uFF5A\u200b-\u200d-]{3,30})", re.I)
RX_REF_BARE = re.compile(r"(?<![\w])(\d{12}|[A-Z]{4}[A-Z0-9]{12,18})(?![\w])")
VALID_REF = re.compile(r"^(\d{12}|[A-Z]{4}[A-Z0-9]{12,18})$")
RX_AMOUNT = re.compile(r"(?:rs\.?|inr|₹|rupees?)\s*([\d,]{2,9}(?:\.\d{1,2})?)|([\d,]{3,9}(?:\.\d{1,2})?)\s*(?:rs\b|rupees?|/-|₹)", re.I)
RX_LOAN_ID = re.compile(r"\b([A-Z]{1,4}-\d{3,8}(?:-[A-Z0-9]{1,4})?)\b", re.I)


# Typo tolerance: a word that is not in the vocabulary but is ONE edit (insert / delete / substitute /
# swap) away from exactly one keyword is read as that keyword ("ballance", "statemnt", "gaat").
VOCAB = frozenset("""balance outstanding statement details detail payment reference number galat chahiye more time
link owe paid done loan please yesterday unsubscribe pay much emi bakaya baki kitna noc callback hospital accident dispute wrong amount
already transfer diya dunga karunga tarikh naukri salary agent person waive penalty""".split())


def _one_edit(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def fix_typos(text: str) -> str:
    def fix(m: re.Match) -> str:
        w = m.group(0)
        lw = w.lower()
        if len(lw) < 3 or lw in VOCAB:
            return w
        hits = [v for v in VOCAB if max(len(v), len(lw)) >= 4 and _one_edit(lw, v)]
        return hits[0] if len(hits) == 1 else w
    return re.sub(r"[A-Za-z]+", fix, text)


@dataclass
class Understanding:
    intent: str
    k09: Optional[k09.IntentResult]
    lang_request: Optional[str] = None
    agreed_date: Optional[str] = None
    menu_pick: Optional[str] = None


def detect_lang(text: str) -> Optional[str]:
    """'hi' for Devanagari or Hinglish (2+ Hinglish function words), 'en' for plain English, None if unsure."""
    if not text:
        return None
    if _DEVANAGARI.search(text):
        return "hi"
    if len(_HINGLISH.findall(text)) >= 2:
        return "hi"
    if re.search(r"[A-Za-z]{3,}", text):
        return "en"
    return None


def language_request(text: str) -> Optional[str]:
    """Explicit 'Hindi mein baat karo' / 'English please' -> 'hi' / 'en'."""
    t = text or ""
    if _LANG_HI.search(t) and _LANG_SWITCH_CUE.search(t):
        return "hi"
    if _LANG_EN.search(t) and _LANG_SWITCH_CUE.search(t):
        return "en"
    return None


def extract_otp(text: str) -> Optional[str]:
    if not text:
        return None
    m = RX_OTP.search(text)
    if m:
        return m.group(1)
    m = RX_OTP_SPACED.search(text)
    if m and len(text.strip()) <= 20:
        return re.sub(r"\D", "", m.group(1))
    return None


def extract_reference(text: str) -> Optional[str]:
    """The bank reference the borrower quotes, as typed (format is judged by fraud.check_reference)."""
    if not text:
        return None
    m = RX_REF_CUE.search(text)
    if m:
        return m.group(1).strip("-")
    m = RX_REF_BARE.search(text)
    return m.group(1) if m else None


def extract_amount_paise(text: str) -> Optional[int]:
    if not text:
        return None
    m = RX_AMOUNT.search(text)
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").replace(",", "")
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return None


def extract_loan_ids(text: str) -> List[str]:
    return [m.group(1).upper() for m in RX_LOAN_ID.finditer(text or "")]


_PAID_WHEN = [(re.compile(p, re.I), d) for p, d in [
    (r"\b(today|aaj)\b", 0), (r"\b(yesterday|kal)\b", -1), (r"\b(parso|parson|day before yesterday)\b", -2),
    (r"\blast week\b|\bpichhle hafte\b|\bpichle hafte\b", -7), (r"\blast month\b|\bpichhle mahine\b|\bpichle mahine\b", -30),
]]


def claimed_payment_date(text: str, today: date) -> Optional[date]:
    """When the borrower says they paid. 'kal' in a paid-claim means yesterday."""
    if not text:
        return None
    for rx, d in _PAID_WHEN:
        if rx.search(text):
            return today + timedelta(days=d)
    m = re.search(r"\b(\d{1,2})\s*(?:days?|din)\s*(?:ago|pehle|pahle)\b", text, re.I)
    if m:
        return today - timedelta(days=int(m.group(1)))
    got, _ = k09.extract_date(text, today)
    if got and got > today:          # extract_date looks forward; a paid-claim date is in the past
        y, m = got.year, got.month
        for _ in range(12):          # same day-of-month, stepping back a month at a time
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
            try:
                cand = got.replace(year=y, month=m)
            except ValueError:
                continue
            if cand <= today:
                return cand
        return None
    return got


class Nlu:
    def __init__(self, classifier: Optional[k09.IntentClassifier] = None):
        self.classifier = classifier or k09.IntentClassifier(llm=None)

    def understand(self, text: str, at: datetime, verified: bool, known_names: Sequence[str] = ()) -> Understanding:
        t = fix_typos(" ".join((text or "").split()))
        if not t:
            return Understanding(k09.OTHER, None)
        if k09.is_stop(t):
            return Understanding(k09.STOP, None)
        lang = language_request(t)
        if verified and t.strip() in MENU:
            return Understanding(MENU[t.strip()], None, menu_pick=t.strip())
        if _RESEND.search(t):
            return Understanding(RESEND_OTP, None)
        if _GREETING.match(t):
            return Understanding(GREETING, None)
        if _THANKS.match(t):
            return Understanding(THANKS, None)
        rules = set(k09.rule_matches(t))
        # Safety intents from K09 rules outrank servicing keywords ("lost my job, what's my balance")
        for safe in (k09.WRONG_PERSON, k09.HARDSHIP, k09.DISPUTE):
            if safe in rules:
                return Understanding(safe, None)
        if any(rx.search(t) for rx in _SERVICING["DISPUTE"]):
            return Understanding(k09.DISPUTE, None)
        if extract_reference(t) and re.search(r"\b(utr|rrn|ref|reference|txn|transaction|paid|pay kar|bhar|jama|payment)\b", t, re.I):
            return Understanding(k09.CLAIMS_PAID, None)
        if k09.CLAIMS_PAID in rules and not _BROKEN_LINK.search(t):
            return Understanding(k09.CLAIMS_PAID, None)
        for name in _ORDER:
            if any(rx.search(t) for rx in _SERVICING[name]):
                return Understanding(name, None)
        if k09.CALLBACK in rules:
            return Understanding(k09.CALLBACK, None)
        if lang and len(t.split()) <= 6:
            return Understanding(LANG_CHANGE, None, lang_request=lang)
        r = self.classifier.classify(k09.InboundMessage(borrower_id="cm03", channel="whatsapp", received_at=at,
                                                        text=t), known_names=known_names)
        st = getattr(r, "stack", None)
        if st and r.intent == k09.OTHER and not st.get("needs_human") and st.get("label") in STACK_SERVICING:
            # use_stack=True only: the tiered stack also names servicing asks (e.g. a Devanagari balance question
            # its small model recognised); K09's legacy intents have no slot for them, so map them here.
            return Understanding(STACK_SERVICING[st["label"]], r)
        return Understanding(r.intent, r, agreed_date=r.agreed_date)
