"""K22: deterministic collections-compliance checklist for the template catalog.

Every template in policy/K22-template-catalog/catalog.json is run through the same
rules; a template is marked APPROVED_FOR_DEMO only when every applicable rule passes.
"Approved" here means *passed this checklist for the synthetic demo*. It is not the
lender's compliance/legal sign-off, DLT registration or Meta template approval.

The rules follow Indian collections conduct norms:
- RBI Fair Practices Code for lenders and RBI guidance on recovery agents / digital
  lending: identify the lender, no intimidation, harassment, humiliation or threats,
  no disclosure of the debt to third parties (family, employer, neighbours),
  only consequences the loan agreement allows;
- RBI circular on penal charges (Aug 2023, effective 1 Jan 2024): lenders levy
  "penal charges", not "penal interest", so "penal interest" is a misleading term;
- TRAI TCCCPR 2018: SMS go out under a registered header (sender id) with a
  DLT-registered content template; variables are declared in the template.

Rules (ids are stable; reports and the catalog's approval.checklist use them):

  LENDER_IDENTIFIED        {{lender_name}} appears in the message (SMS: also a sender id)
  NO_THREATS_OR_ABUSE      no threat, intimidation, abuse or shaming language
  CONSEQUENCES_PERMITTED   only agreement-backed consequences (late fee, penal charges,
                           bureau reporting, next stage of collections), introduced by tier:
                           T1 none, T2 late fee, T3 + penal charges + bureau, T4 + next stage;
                           acknowledgements and pre-due/NACH notices carry none
  NO_THIRD_PARTY_DISCLOSURE  no mention of telling or contacting anyone but the borrower
  PAY_AND_HELP             a helpline on every message; a way to pay on reminders
  IGNORE_IF_PAID           T1 and pre-due reminders say to ignore the message if already paid
  ACCOUNT_LAST4_ONLY       account shown masked by its last 4 digits only; no long digit runs
  IDENTITY_STAGE           the template's identity stage matches its purpose; pre-verification
                           replies carry no amount/account/date details; receipts are
                           post-verification and say so; IVR blasts and voice openings check
                           the recipient before any loan detail
  LENGTH_LIMITS            SMS en: GSM-7 only and <= 160 chars at maximum placeholder length;
                           SMS hi: Unicode, <= 4 segments (268 UCS-2 units); WhatsApp body
                           <= 1024; email subject <= 120; IVR script <= 700; voice opening <= 200
  PLACEHOLDERS_ALLOWED     only the allowed merge fields; `variables` equals the fields used
  LANGUAGE_PARITY          the en and hi versions of a family exist, use the same placeholders,
                           and hi is written in Devanagari (en has none)
  CHANNEL_REGISTRATION     SMS: dlt_template_id + sender_id; WhatsApp: UTILITY category +
                           template name; email: subject; IVR: recording status;
                           voicebot: Sarvam agent reference + recorded agent controls
  PURPOSE_CONTENT          each purpose says what it must (T3 days overdue, T4 final date,
                           PTP promise date + reminders paused, hardship/STOP no payment ask, ...)
  TIER_BANDS               tier <-> intensity (T1/T2 LOW, T3/T4 STANDARD) and allowed DPD bands

Run:  python -m kairos_backend.template_compliance            # report over the shipped catalog
      python -m kairos_backend.template_compliance --stamp    # rewrite approval blocks in place
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PASS, FAIL, NA = "PASS", "FAIL", "N/A"
APPROVED_STATUS = "APPROVED_FOR_DEMO"
FAILED_STATUS = "FAILED_CHECKLIST"
REVIEWER = "Kairos collections compliance review (automated checklist)"
REVIEW_DATE = "2026-11-19"

RULES = (
    "LENDER_IDENTIFIED", "NO_THREATS_OR_ABUSE", "CONSEQUENCES_PERMITTED", "NO_THIRD_PARTY_DISCLOSURE",
    "PAY_AND_HELP", "IGNORE_IF_PAID", "ACCOUNT_LAST4_ONLY", "IDENTITY_STAGE", "LENGTH_LIMITS",
    "PLACEHOLDERS_ALLOWED", "LANGUAGE_PARITY", "CHANNEL_REGISTRATION", "PURPOSE_CONTENT", "TIER_BANDS",
)

# Merge fields and the maximum rendered length used for length checks.
PLACEHOLDER_MAX_LEN = {
    "name": 20,            # first name
    "lender_name": 15,     # short brand name as registered with DLT / Meta
    "loan_last4": 4,
    "amount": 9,           # 12,34,567 (Indian grouping, no decimals)
    "due_date": 9,         # 19-Nov-26
    "days_overdue": 3,
    "payment_link": 22,    # short https link
    "helpline": 13,        # 1800-123-4567
    "final_date": 9,
    "represent_date": 9,
    "promise_date": 9,
}
ALLOWED_PLACEHOLDERS = frozenset(PLACEHOLDER_MAX_LEN)

TIER_INTENSITY = {1: "LOW", 2: "LOW", 3: "STANDARD", 4: "STANDARD"}
TIER_BANDS_ALLOWED = {1: {"B1", "B2", "B3", "B4"}, 2: {"B1", "B2", "B3", "B4"},
                      3: {"B1", "B2", "B3", "B4"}, 4: {"B2", "B3", "B4"}}

# Expected identity stage per purpose.
OUTBOUND, PRE_VERIFICATION, POST_VERIFICATION, IN_CALL = (
    "outbound_registered_contact", "pre_verification", "post_verification", "in_call_identity_check")
PURPOSE_STAGE = {
    "REMINDER": OUTBOUND, "PRE_DUE_REMINDER": OUTBOUND, "NACH_REPRESENTMENT": OUTBOUND,
    "ASK_PAYMENT_DATE": PRE_VERIFICATION, "PTP_CONFIRM": PRE_VERIFICATION, "CALLBACK_ACK": PRE_VERIFICATION,
    "HARDSHIP_ACK": PRE_VERIFICATION, "STOP_CONFIRM": PRE_VERIFICATION,
    "PAYMENT_RECEIVED": POST_VERIFICATION,
}
# Fields that identify the account or the debt; never in a pre-verification reply.
ACCOUNT_DETAIL_FIELDS = frozenset({"amount", "loan_last4", "days_overdue", "due_date", "final_date", "represent_date"})

# ---------- vocabulary (lower-cased matching; en with word boundaries, hi as substrings) ----------
THREAT_TERMS_EN = (
    "arrest", "jail", "prison", "police", "warrant", "fir", "criminal", "crime", "shame", "shameful",
    "defaulter", "cheat", "cheater", "fraudster", "blacklist", "blacklisted", "strict action", "severe action",
    "last chance", "or else", "consequences will follow", "we will not spare", "idiot", "liar", "useless",
)
THREAT_TERMS_HI = (
    "गिरफ्तार", "गिरफ़्तार", "जेल", "पुलिस", "वारंट", "एफआईआर", "अपराध", "शर्म", "बदनाम", "डिफॉल्टर",
    "डिफ़ॉल्टर", "धोखेबाज", "ब्लैकलिस्ट", "सख्त कार्रवाई", "सख़्त कार्रवाई", "आखिरी मौका", "आख़िरी मौका",
)
# Consequences a personal-loan agreement does not give an automated reminder, or misstated ones.
MISLEADING_CONSEQUENCE_EN = (
    "legal action", "legal notice", "legal proceedings", "court", "lawsuit", "sue", "seize", "seizure",
    "repossess", "repossession", "attach your", "recovery agent", "field visit", "home visit",
    "visit your", "visit you", "at your doorstep", "penal interest",
)
MISLEADING_CONSEQUENCE_HI = (
    "कानूनी कार्रवाई", "क़ानूनी कार्रवाई", "कानूनी नोटिस", "अदालत", "कोर्ट", "मुकदमा", "जब्त", "ज़ब्त",
    "कुर्की", "रिकवरी एजेंट", "घर आ", "घर पर आ", "दफ्तर आ", "दंडात्मक ब्याज",
)
THIRD_PARTY_EN = (
    "employer", "your office", "your workplace", "your family", "family members", "relatives", "neighbour",
    "neighbor", "colleague", "your friends", "your references", "inform your", "contact your", "tell your",
)
THIRD_PARTY_HI = (
    "नियोक्ता", "दफ्तर", "दफ़्तर", "ऑफिस", "परिवार", "रिश्तेदार", "पड़ोसी", "सहकर्मी", "दोस्तों", "मित्रों",
)
# Agreement-backed consequence kinds, introduced tier by tier.
CONSEQUENCE_KINDS = {
    "late_fee": (("late fee", "late payment charge", "late charges"), ("लेट फीस", "लेट पेमेंट शुल्क", "विलंब शुल्क")),
    "penal_charges": (("penal charge",), ("दंडात्मक शुल्क",)),
    "bureau": (("credit bureau", "credit score", "credit history", "cibil"), ("क्रेडिट ब्यूरो", "क्रेडिट स्कोर")),
    "next_stage": (("next stage of collections", "next collections stage"), ("कलेक्शन के अगले चरण",)),
}
TIER_CONSEQUENCES = {1: set(), 2: {"late_fee"}, 3: {"late_fee", "penal_charges", "bureau"},
                     4: {"late_fee", "penal_charges", "bureau", "next_stage"}}

IGNORE_MARKERS = {"en": (("already paid",), ("ignore", "disregard")), "hi": (("भुगतान कर",), ("अनदेखा",))}
MASKED_LAST4 = {"en": ("XX{{loan_last4}}", "ending {{loan_last4}}"),
                "hi": ("XX{{loan_last4}}", "अंतिम 4 अंक {{loan_last4}}")}
IVR_PAY_MARKERS = {"en": ("link sent",), "hi": ("लिंक",)}
NACH_BALANCE_MARKERS = {"en": ("sufficient balance",), "hi": ("पर्याप्त राशि",)}
PAUSE_MARKERS = {"en": ("pause",), "hi": ("रोक",)}
STOP_MARKERS = {"en": ("not receive",), "hi": ("नहीं मिलेंगे",)}
OVERDUE_MARKERS = {"en": ("overdue", "not been received", "pending since"), "hi": ("बकाया", "प्राप्त नहीं")}
VERIFIED_MARKERS = ("verif",)
# A voice opening may name the lender and the borrower, nothing about the debt.
VOICE_DEBT_TERMS = {"en": ("loan", "emi", "payment", "overdue", "due", "amount", "rupees"),
                    "hi": ("लोन", "ईएमआई", "EMI", "भुगतान", "बकाया", "राशि", "रुपये")}
VOICE_RECIPIENT_MARKERS = {"en": ("this message is for {{name}}",), "hi": ("यह संदेश {{name}} के लिए है",)}
REQUIRED_AGENT_CONTROLS = frozenset({
    "identity_before_disclosure", "no_amounts_or_account_numbers", "real_consequences_only",
    "no_threats_or_third_party_contact", "stop_request_honoured", "human_callback_offered",
})

GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = set("^{}\\[~]|€")
SMS_EN_MAX, SMS_HI_MAX_SEGMENTS, WA_MAX, EMAIL_SUBJECT_MAX, IVR_MAX, VOICE_OPENING_MAX = 160, 4, 1024, 120, 700, 200

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
LONG_DIGITS_RE = re.compile(r"\d{5,}")
MESSAGE_TYPES = {"sms": "sms", "whatsapp": "whatsapp", "email": "email", "ivr": "ivr_blast", "voicebot": "voicebot_script"}


@dataclass
class ComplianceResult:
    template_id: str
    checklist: Dict[str, str] = field(default_factory=dict)   # rule -> PASS | FAIL | N/A
    reasons: List[str] = field(default_factory=list)           # "RULE: why", one per failure

    @property
    def passed(self) -> bool:
        return all(v != FAIL for v in self.checklist.values())

    @property
    def failed_rules(self) -> List[str]:
        return [r for r, v in self.checklist.items() if v == FAIL]

    def as_dict(self) -> dict:
        return {"template_id": self.template_id, "passed": self.passed, "checklist": dict(self.checklist),
                "reasons": list(self.reasons)}


# ---------- helpers ----------
def placeholders(text: str) -> List[str]:
    return PLACEHOLDER_RE.findall(text or "")


# Unfilled slots that must never reach a borrower (E2E QA, 20 Nov 2026: the reply model wrote "A team member at
# [lender name] has your request" and it was sent). Checked on rendered / rephrased text, never on templates.
UNFILLED_RE = re.compile(
    r"\[[^\[\]\n]{1,40}\]"                                  # [lender name], [account]
    r"|\{\{?\s*[A-Za-z_][\w .-]{0,40}\}?\}"                   # {lender}, {{lender_name}}
    r"|<\s*[A-Za-z_][\w .-]{0,40}>"                           # <lender>, <name>
    r"|\bX{4,}\b|_{3,}"                                       # XXXX, ____
    r"|\b(?:lender|company|bank|borrower|customer|agent)(?:'s)?\s+name\b"
    r"|\blender\s+ka\s+naam\b|ऋणदाता\s+का\s+नाम", re.I)


def unfilled_placeholders(text: str) -> list:
    """Every unfilled template slot in `text` (rendered or model-rephrased), as found; [] when clean."""
    return [m.group(0) for m in UNFILLED_RE.finditer(text or "")]


def render_max(text: str) -> str:
    """Render placeholders at their maximum length (unknown ones at 20) for length checks."""
    return PLACEHOLDER_RE.sub(lambda m: "x" * PLACEHOLDER_MAX_LEN.get(m.group(1), 20), text or "")


def is_gsm7(text: str) -> bool:
    return all(ch in GSM7_BASIC or ch in GSM7_EXT for ch in text)


def gsm7_length(text: str) -> int:
    return sum(2 if ch in GSM7_EXT else 1 for ch in text)


def ucs2_units(text: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def sms_segments(text: str, unicode: bool) -> int:
    n = ucs2_units(text) if unicode else gsm7_length(text)
    single, multi = (70, 67) if unicode else (160, 153)
    return 1 if n <= single else -(-n // multi)


def _has(text: str, terms: Iterable[str], lang: str) -> List[str]:
    """Terms found in text. English terms match on word boundaries, Hindi as substrings."""
    low = (text or "").lower()
    found = []
    for t in terms:
        if DEVANAGARI_RE.search(t) or lang == "hi" and not t.isascii():
            if t.lower() in low:
                found.append(t)
        elif re.search(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?:s|es)?(?![a-z0-9])", low):
            found.append(t)
    return found


def _contains(text: str, markers: Iterable[str]) -> bool:
    """Case-insensitive substring match for required wording (markers, not vocabulary)."""
    low = (text or "").lower()
    return any(m.lower() in low for m in markers)


def _texts(row: dict) -> Dict[str, str]:
    """The customer-facing text fields of a row."""
    out = {}
    for k in ("subject", "body", "opening_line"):
        if isinstance(row.get(k), str) and row[k]:
            out[k] = row[k]
    return out


def _all_text(row: dict) -> str:
    return "\n".join(_texts(row).values())


def consequence_kinds(text: str, lang: str) -> set:
    kinds = set()
    for kind, (en, hi) in CONSEQUENCE_KINDS.items():
        if _has(text, en, "en") or _has(text, hi, "hi"):
            kinds.add(kind)
    return kinds


def _family_key(row: dict) -> Optional[str]:
    return row.get("family") if isinstance(row.get("family"), str) and row.get("family") else None


# ---------- the checklist ----------
def check_template(row: dict, catalog_rows: Optional[List[dict]] = None) -> ComplianceResult:
    """Run every rule over one raw catalog row. `catalog_rows` (the whole catalog) is needed
    for LANGUAGE_PARITY; without it parity is N/A."""
    tid = str(row.get("id", "<no id>"))
    res = ComplianceResult(tid)
    ttype = row.get("type") or MESSAGE_TYPES.get(row.get("channel"), "")
    lang = row.get("lang", "")
    purpose = row.get("purpose", "")
    tier = row.get("tier")
    text = _all_text(row)
    body = row.get("body") or ""
    used = placeholders(text)
    used_set = set(used)
    voice_script = ttype == "voicebot_script"

    def put(rule: str, problems: List[str], applicable: bool = True):
        if not applicable:
            res.checklist[rule] = NA
            return
        res.checklist[rule] = FAIL if problems else PASS
        res.reasons.extend(f"{rule}: {p}" for p in problems)

    # LENDER_IDENTIFIED
    p = []
    if "lender_name" not in used_set:
        p.append("message does not name the lender ({{lender_name}} missing)")
    if ttype == "sms" and not str(row.get("sender_id") or "").strip():
        p.append("SMS has no sender_id (DLT header)")
    put("LENDER_IDENTIFIED", p)

    # NO_THREATS_OR_ABUSE
    hits = _has(text, THREAT_TERMS_EN, "en") + _has(text, THREAT_TERMS_HI, "hi")
    put("NO_THREATS_OR_ABUSE", [f"threat/intimidation/shaming term {h!r}" for h in hits])

    # CONSEQUENCES_PERMITTED
    p = [f"consequence not permitted for an automated reminder or misstated: {h!r}"
         for h in _has(text, MISLEADING_CONSEQUENCE_EN, "en") + _has(text, MISLEADING_CONSEQUENCE_HI, "hi")]
    kinds = consequence_kinds(text, lang)
    allowed = TIER_CONSEQUENCES.get(tier, set()) if purpose == "REMINDER" and isinstance(tier, int) else set()
    if purpose == "REMINDER" and voice_script:
        allowed = set()   # the opening line carries no consequence; the agent's controls cover the call
    extra = sorted(kinds - allowed)
    if extra:
        where = f"tier {tier}" if purpose == "REMINDER" and tier else purpose
        p.append(f"{where} may not mention {', '.join(extra)}")
    put("CONSEQUENCES_PERMITTED", p)

    # NO_THIRD_PARTY_DISCLOSURE
    hits = _has(text, THIRD_PARTY_EN, "en") + _has(text, THIRD_PARTY_HI, "hi")
    put("NO_THIRD_PARTY_DISCLOSURE", [f"mentions a third party: {h!r}" for h in hits])

    # PAY_AND_HELP
    p = []
    if not voice_script:
        if "helpline" not in used_set:
            p.append("no helpline ({{helpline}} missing)")
        if purpose in ("REMINDER", "PRE_DUE_REMINDER"):
            if ttype == "ivr_blast":
                if not _contains(body, IVR_PAY_MARKERS.get(lang, ())):
                    p.append("IVR script does not say how to pay (link sent by SMS/WhatsApp)")
            elif "payment_link" not in used_set:
                p.append("reminder has no way to pay ({{payment_link}} missing)")
        if purpose == "NACH_REPRESENTMENT" and not _contains(body, NACH_BALANCE_MARKERS.get(lang, ())):
            p.append("NACH notice does not ask to keep sufficient balance")
    put("PAY_AND_HELP", p, applicable=not voice_script)

    # IGNORE_IF_PAID
    needs_ignore = (purpose == "REMINDER" and tier == 1) or purpose == "PRE_DUE_REMINDER"
    p = []
    if needs_ignore:
        need_a, need_b = IGNORE_MARKERS.get(lang, ((), ()))
        if not (_contains(body, need_a) and _contains(body, need_b)):
            p.append("T1/pre-due reminder must say to ignore it if already paid")
    put("IGNORE_IF_PAID", p, applicable=needs_ignore)

    # ACCOUNT_LAST4_ONLY
    p = []
    bad_fields = sorted(f for f in used_set if f != "loan_last4" and re.search(r"(loan|account|acct|acc)_?(no|num|number|id)", f))
    if bad_fields:
        p.append(f"full account/loan number field(s) {bad_fields}; use {{{{loan_last4}}}}")
    literal = PLACEHOLDER_RE.sub("", text)
    if LONG_DIGITS_RE.search(literal):
        p.append("literal digit run of 5+ digits (looks like an account or phone number)")
    if "loan_last4" in used_set:
        markers = MASKED_LAST4.get(lang, ())
        if not any(m in text for m in markers):
            p.append("{{loan_last4}} is not shown as masked (e.g. 'XX{{loan_last4}}' / 'ending {{loan_last4}}')")
    put("ACCOUNT_LAST4_ONLY", p)

    # IDENTITY_STAGE
    p = []
    stage = row.get("identity_stage")
    expected = IN_CALL if voice_script else PURPOSE_STAGE.get(purpose)
    if stage != expected:
        p.append(f"identity_stage {stage!r} does not match purpose {purpose} (expected {expected!r})")
    if expected == PRE_VERIFICATION:
        leaked = sorted(used_set & ACCOUNT_DETAIL_FIELDS)
        if leaked:
            p.append(f"pre-verification reply carries account/debt details {leaked}")
    if expected == POST_VERIFICATION:
        cond = str(row.get("send_condition") or "").lower()
        if not any(m in cond for m in VERIFIED_MARKERS):
            p.append("post-verification template must state its verification send_condition")
    if ttype == "ivr_blast" and not any(m.lower() in body.lower() for m in VOICE_RECIPIENT_MARKERS.get(lang, ())):
        p.append("IVR blast must open by naming the intended recipient before any loan detail")
    if voice_script:
        opening = row.get("opening_line") or ""
        debt = _has(opening, VOICE_DEBT_TERMS.get(lang, ()), lang)
        if debt:
            p.append(f"voice opening mentions the debt before identity is confirmed: {debt}")
        if "{{name}}" not in opening or "?" not in opening:
            p.append("voice opening must ask to confirm it is speaking with {{name}}")
    put("IDENTITY_STAGE", p)

    # LENGTH_LIMITS
    p = []
    if ttype == "sms":
        rendered = render_max(body)
        if lang == "hi":
            segs = sms_segments(rendered, unicode=True)
            if segs > SMS_HI_MAX_SEGMENTS:
                p.append(f"Hindi SMS is {ucs2_units(rendered)} UCS-2 units = {segs} segments (> {SMS_HI_MAX_SEGMENTS})")
        else:
            if not is_gsm7(rendered):
                bad = sorted({c for c in rendered if c not in GSM7_BASIC and c not in GSM7_EXT})
                p.append(f"English SMS has non GSM-7 characters {bad} (would be sent as Unicode)")
            elif gsm7_length(rendered) > SMS_EN_MAX:
                p.append(f"English SMS is {gsm7_length(rendered)} chars at max placeholder length (> {SMS_EN_MAX})")
    elif ttype == "whatsapp" and len(render_max(body)) > WA_MAX:
        p.append(f"WhatsApp body exceeds {WA_MAX} chars")
    elif ttype == "email" and len(render_max(row.get("subject") or "")) > EMAIL_SUBJECT_MAX:
        p.append(f"email subject exceeds {EMAIL_SUBJECT_MAX} chars")
    elif ttype == "ivr_blast" and len(render_max(body)) > IVR_MAX:
        p.append(f"IVR script exceeds {IVR_MAX} chars")
    elif voice_script and len(render_max(row.get("opening_line") or "")) > VOICE_OPENING_MAX:
        p.append(f"voice opening exceeds {VOICE_OPENING_MAX} chars")
    put("LENGTH_LIMITS", p)

    # PLACEHOLDERS_ALLOWED
    p = []
    unknown = sorted(used_set - ALLOWED_PLACEHOLDERS)
    if unknown:
        p.append(f"unknown placeholder(s) {unknown}")
    declared = row.get("variables")
    if not isinstance(declared, list) or sorted(set(declared)) != sorted(used_set):
        p.append(f"`variables` {declared} does not equal the placeholders used {sorted(used_set)}")
    stray = PLACEHOLDER_RE.sub("", text)
    if "{" in stray or "}" in stray or "[" in stray and "]" in stray:
        p.append("malformed or bracket-style placeholder left in text")
    put("PLACEHOLDERS_ALLOWED", p)

    # LANGUAGE_PARITY
    p = []
    if lang == "hi" and not DEVANAGARI_RE.search(PLACEHOLDER_RE.sub("", text)):
        p.append("hi template is not written in Devanagari")
    if lang == "en" and DEVANAGARI_RE.search(text):
        p.append("en template contains Devanagari")
    if catalog_rows is not None:
        fam = _family_key(row)
        if not fam:
            p.append("no `family` to pair the en/hi versions")
        else:
            other = "hi" if lang == "en" else "en"
            pair = [r for r in catalog_rows if r.get("family") == fam and r.get("lang") == other]
            if not pair:
                p.append(f"no {other} version in family {fam}")
            else:
                theirs = set(placeholders(_all_text(pair[0])))
                if theirs != used_set:
                    p.append(f"placeholders differ from the {other} version: {sorted(used_set ^ theirs)}")
    put("LANGUAGE_PARITY", p)

    # CHANNEL_REGISTRATION
    p = []
    if ttype != MESSAGE_TYPES.get(row.get("channel")):
        p.append(f"type {ttype!r} does not fit channel {row.get('channel')!r}")
    if ttype == "sms":
        for k in ("dlt_template_id", "sender_id"):
            if not str(row.get(k) or "").strip():
                p.append(f"SMS missing {k}")
    elif ttype == "whatsapp":
        if row.get("wa_category") != "UTILITY":
            p.append("WhatsApp collections templates must be category UTILITY")
        if not re.fullmatch(r"[a-z0-9_]{1,512}", str(row.get("wa_template_name") or "")):
            p.append("wa_template_name must be lowercase letters, digits and underscores")
    elif ttype == "email":
        if not str(row.get("subject") or "").strip():
            p.append("email has no subject")
    elif ttype == "ivr_blast":
        if not str(row.get("recording_status") or "").strip():
            p.append("IVR blast has no recording_status")
    elif voice_script:
        agent = row.get("sarvam_agent") or {}
        for k in ("name", "app_id", "prompt_sha"):
            if not str(agent.get(k) or "").strip():
                p.append(f"sarvam_agent.{k} missing")
        missing = sorted(REQUIRED_AGENT_CONTROLS - set(row.get("agent_controls") or []))
        if missing:
            p.append(f"agent controls not recorded: {missing}")
    put("CHANNEL_REGISTRATION", p)

    # PURPOSE_CONTENT
    p = []
    if purpose == "REMINDER" and not voice_script:
        if tier == 3 and "days_overdue" not in used_set:
            p.append("T3 must state days overdue ({{days_overdue}})")
        if tier == 4:
            if "final_date" not in used_set:
                p.append("T4 must give a hard date ({{final_date}})")
            if "next_stage" not in kinds:
                p.append("T4 must state the consequence of missing the date (next stage of collections)")
    elif purpose == "PRE_DUE_REMINDER":
        if "due_date" not in used_set:
            p.append("pre-due reminder must give the due date")
        if _contains(body, OVERDUE_MARKERS.get(lang, ())):
            p.append("pre-due reminder must not describe the EMI as overdue")
    elif purpose == "NACH_REPRESENTMENT":
        if "represent_date" not in used_set:
            p.append("NACH notice must give the re-presentment date")
    elif purpose == "PTP_CONFIRM":
        if "promise_date" not in used_set:
            p.append("PTP confirmation must repeat the promised date")
        if not _contains(body, PAUSE_MARKERS.get(lang, ())):
            p.append("PTP confirmation must say reminders pause until then")
    elif purpose == "HARDSHIP_ACK":
        if used_set & {"payment_link", "amount"}:
            p.append("hardship acknowledgement must not ask for payment")
    elif purpose == "STOP_CONFIRM":
        if "payment_link" in used_set:
            p.append("STOP confirmation must not ask for payment")
        if not _contains(body, STOP_MARKERS.get(lang, ())):
            p.append("STOP confirmation must say no further reminders will be sent")
    elif purpose == "PAYMENT_RECEIVED":
        if "payment_link" in used_set:
            p.append("receipt must not carry a payment link")
        if "amount" not in used_set:
            p.append("receipt must state the amount received")
    put("PURPOSE_CONTENT", p)

    # TIER_BANDS
    p = []
    bands = set(row.get("dpd_bands") or [])
    if purpose == "REMINDER" and not voice_script:
        if tier not in TIER_INTENSITY:
            p.append("REMINDER message templates need tier 1-4")
        else:
            if row.get("intensity") != TIER_INTENSITY[tier]:
                p.append(f"tier {tier} must be intensity {TIER_INTENSITY[tier]}")
            if bands - TIER_BANDS_ALLOWED[tier]:
                p.append(f"tier {tier} may not be used in {sorted(bands - TIER_BANDS_ALLOWED[tier])}")
    elif tier is not None:
        p.append("only REMINDER message templates carry a tier")
    if purpose == "REMINDER" and "PRE_DUE" in bands:
        p.append("REMINDER may not list PRE_DUE (use PRE_DUE_REMINDER)")
    if purpose == "PRE_DUE_REMINDER" and bands != {"PRE_DUE"}:
        p.append("PRE_DUE_REMINDER must list only PRE_DUE")
    if purpose == "NACH_REPRESENTMENT" and "PRE_DUE" in bands:
        p.append("NACH re-presentment follows a bounce; not PRE_DUE")
    put("TIER_BANDS", p)

    return res


def check_catalog(data: Any) -> List[ComplianceResult]:
    rows = data["templates"] if isinstance(data, dict) else list(data)
    return [check_template(r, rows) for r in rows]


# What real approval still needs, per template type (goes into approval.notes).
NEXT_STEPS = {
    "sms": "DLT content-template registration under the lender's header (dlt_template_id, sender_id)",
    "whatsapp": "Meta UTILITY template approval (wa_template_name)",
    "email": "sending-domain setup and the lender's email footer/disclaimer",
    "ivr_blast": "recording the approved script (recording_status)",
    "voicebot_script": "re-review whenever the Sarvam agent prompt changes (prompt_sha) and deployment of the agent",
}


def approval_notes(row: dict, result: ComplianceResult) -> str:
    ttype = row.get("type") or MESSAGE_TYPES.get(row.get("channel"), "")
    if result.passed:
        return ("Passed the automated collections-compliance checklist for the synthetic demo. Before real use: the "
                f"lender's compliance/legal sign-off and {NEXT_STEPS.get(ttype, 'provider registration')}.")
    return (f"Not approved: failed {', '.join(result.failed_rules)}. Never selected; the Gate's TEMPLATE_APPROVED "
            "check blocks it.")


def approval_block(result: ComplianceResult, row: Optional[dict] = None) -> dict:
    status = APPROVED_STATUS if result.passed else FAILED_STATUS
    return {"status": status, "reviewed_by": REVIEWER, "reviewed_on": REVIEW_DATE,
            "checklist": dict(result.checklist), "failures": list(result.reasons),
            "notes": approval_notes(row or {}, result)}


def stamp(data: dict) -> dict:
    """Recompute every approval block and the `approved` flag from the checklist."""
    results = {r.template_id: r for r in check_catalog(data)}
    for row in data["templates"]:
        r = results[row["id"]]
        row["approval"] = approval_block(r, row)
        row["approved"] = r.passed
    return data


def report(results: List[ComplianceResult]) -> str:
    lines = []
    ok = sum(r.passed for r in results)
    lines.append(f"{ok}/{len(results)} templates pass the collections-compliance checklist")
    for r in results:
        if not r.passed:
            lines.append(f"  FAIL {r.template_id}")
            lines.extend(f"       - {x}" for x in r.reasons)
    by_rule = defaultdict(int)
    for r in results:
        for rule in r.failed_rules:
            by_rule[rule] += 1
    if by_rule:
        lines.append("failures by rule: " + ", ".join(f"{k}={v}" for k, v in sorted(by_rule.items())))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    from .templates import default_catalog_path
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?", default=None)
    ap.add_argument("--stamp", action="store_true", help="rewrite approval blocks and approved flags in place")
    ap.add_argument("--json", action="store_true", help="print per-template results as JSON")
    a = ap.parse_args(argv)
    path = Path(a.path) if a.path else default_catalog_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    if a.stamp:
        stamp(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    results = check_catalog(data)
    print(json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2) if a.json else report(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
